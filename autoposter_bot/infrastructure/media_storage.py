from __future__ import annotations

import mimetypes
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, BinaryIO, Iterator
from urllib.parse import urlparse
from uuid import uuid4

from autoposter_bot.config import Settings
from autoposter_bot.domain.content import MediaAsset, PlatformVariant


REMOTE_PULL_PLATFORMS = {"instagram", "tiktok"}


@dataclass(frozen=True, slots=True)
class StoredMedia:
    source: str
    storage_key: str
    media_type: str
    content_type: str
    original_name: str


class MediaStorage:
    backend: str

    def put(
        self,
        *,
        workspace_id: int,
        filename: str,
        fileobj: BinaryIO,
        content_type: str | None,
    ) -> StoredMedia:
        raise NotImplementedError

    @contextmanager
    def resolve_variant(self, variant: PlatformVariant, platform: str) -> Iterator[PlatformVariant]:
        yield variant


class LocalMediaStorage(MediaStorage):
    backend = "local"

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, *, workspace_id: int, filename: str, fileobj: BinaryIO, content_type: str | None) -> StoredMedia:
        safe_name = _safe_filename(filename)
        key = f"workspace-{workspace_id}/{uuid4().hex}-{safe_name}"
        target = (self.root / key).resolve()
        if self.root not in target.parents:
            raise ValueError("Invalid media path")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as output:
            shutil.copyfileobj(fileobj, output)
        resolved_type = content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        return StoredMedia(
            source=str(target),
            storage_key=key,
            media_type=_media_type(resolved_type, safe_name),
            content_type=resolved_type,
            original_name=safe_name,
        )


class S3MediaStorage(MediaStorage):
    backend = "s3"

    def __init__(self) -> None:
        import boto3
        from botocore.config import Config

        bucket = os.getenv("AUTOPOSTER_S3_BUCKET", "").strip()
        if not bucket:
            raise RuntimeError("AUTOPOSTER_S3_BUCKET is required for S3 media storage")
        self.bucket = bucket
        self.region = os.getenv("AUTOPOSTER_S3_REGION", "us-east-1").strip() or "us-east-1"
        endpoint_url = os.getenv("AUTOPOSTER_S3_ENDPOINT_URL", "").strip() or None
        self.presign_seconds = max(900, int(os.getenv("AUTOPOSTER_MEDIA_PRESIGN_SECONDS", "86400")))
        self.client = boto3.client(
            "s3",
            region_name=self.region,
            endpoint_url=endpoint_url,
            aws_access_key_id=os.getenv("AUTOPOSTER_S3_ACCESS_KEY_ID") or None,
            aws_secret_access_key=os.getenv("AUTOPOSTER_S3_SECRET_ACCESS_KEY") or None,
            config=Config(signature_version="s3v4"),
        )

    def put(self, *, workspace_id: int, filename: str, fileobj: BinaryIO, content_type: str | None) -> StoredMedia:
        safe_name = _safe_filename(filename)
        key = f"workspace-{workspace_id}/{uuid4().hex}-{safe_name}"
        resolved_type = content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        self.client.upload_fileobj(
            fileobj,
            self.bucket,
            key,
            ExtraArgs={"ContentType": resolved_type},
        )
        return StoredMedia(
            source=f"s3://{self.bucket}/{key}",
            storage_key=key,
            media_type=_media_type(resolved_type, safe_name),
            content_type=resolved_type,
            original_name=safe_name,
        )

    def presign(self, source: str) -> str:
        bucket, key = _parse_s3_source(source)
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=self.presign_seconds,
        )

    def download(self, source: str, target: Path) -> None:
        bucket, key = _parse_s3_source(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(bucket, key, str(target))

    @contextmanager
    def resolve_variant(self, variant: PlatformVariant, platform: str) -> Iterator[PlatformVariant]:
        if not any(asset.source.startswith("s3://") for asset in variant.media):
            yield variant
            return

        temp_dir: tempfile.TemporaryDirectory[str] | None = None
        resolved: list[MediaAsset] = []
        try:
            for asset in variant.media:
                if not asset.source.startswith("s3://"):
                    resolved.append(asset)
                    continue
                if platform.lower() in REMOTE_PULL_PLATFORMS:
                    source = self.presign(asset.source)
                else:
                    if temp_dir is None:
                        temp_dir = tempfile.TemporaryDirectory(prefix="autoposter-media-")
                    name = str(asset.metadata.get("original_name") or Path(urlparse(asset.source).path).name or asset.id)
                    target = Path(temp_dir.name) / _safe_filename(name)
                    self.download(asset.source, target)
                    source = str(target)
                resolved.append(replace(asset, source=source))
            yield replace(variant, media=resolved)
        finally:
            if temp_dir is not None:
                temp_dir.cleanup()


def build_media_storage(settings: Settings) -> MediaStorage:
    backend = os.getenv("AUTOPOSTER_MEDIA_BACKEND", "local").strip().lower()
    if backend == "s3":
        return S3MediaStorage()
    if backend != "local":
        raise RuntimeError(f"Unsupported AUTOPOSTER_MEDIA_BACKEND: {backend}")
    root = Path(os.getenv("AUTOPOSTER_MEDIA_ROOT", "").strip() or settings.database_path.parent / "media")
    return LocalMediaStorage(root)


def _safe_filename(filename: str) -> str:
    base = Path(filename or "media.bin").name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip(".-")
    return cleaned[:160] or "media.bin"


def _media_type(content_type: str, filename: str) -> str:
    if content_type.startswith("image/"):
        return "image"
    if content_type.startswith("video/"):
        return "video"
    suffix = Path(filename).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return "image"
    if suffix in {".mp4", ".mov", ".mkv", ".webm", ".avi"}:
        return "video"
    return "file"


def _parse_s3_source(source: str) -> tuple[str, str]:
    parsed = urlparse(source)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.strip("/"):
        raise ValueError(f"Invalid S3 media source: {source}")
    return parsed.netloc, parsed.path.lstrip("/")
