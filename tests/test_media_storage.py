from __future__ import annotations

import io
from pathlib import Path

from autoposter_bot.domain.content import MediaAsset, PlatformVariant
from autoposter_bot.infrastructure.media_storage import LocalMediaStorage, S3MediaStorage


def test_local_media_storage_writes_workspace_file(tmp_path):
    storage = LocalMediaStorage(tmp_path / "media")
    stored = storage.put(
        workspace_id=7,
        filename="photo test.jpg",
        fileobj=io.BytesIO(b"image-bytes"),
        content_type="image/jpeg",
    )

    assert stored.media_type == "image"
    assert stored.storage_key.startswith("workspace-7/")
    assert Path(stored.source).read_bytes() == b"image-bytes"
    assert "photo-test.jpg" in stored.storage_key


def test_s3_variant_uses_presigned_url_for_pull_platform(monkeypatch):
    storage = object.__new__(S3MediaStorage)
    monkeypatch.setattr(storage, "presign", lambda source: "https://signed.example/video.mp4")
    variant = PlatformVariant(
        content_id="content-1",
        platform="instagram",
        text="Caption",
        media=[MediaAsset(source="s3://bucket/workspace-1/video.mp4", media_type="video")],
    )

    with storage.resolve_variant(variant, "instagram") as resolved:
        assert resolved.media[0].source == "https://signed.example/video.mp4"
    assert variant.media[0].source.startswith("s3://")


def test_s3_variant_downloads_temp_file_for_file_upload_platform(monkeypatch):
    storage = object.__new__(S3MediaStorage)

    def fake_download(source: str, target: Path) -> None:
        target.write_bytes(b"video")

    monkeypatch.setattr(storage, "download", fake_download)
    variant = PlatformVariant(
        content_id="content-1",
        platform="vk",
        text="Caption",
        media=[
            MediaAsset(
                source="s3://bucket/workspace-1/video.mp4",
                media_type="video",
                metadata={"original_name": "video.mp4"},
            )
        ],
    )

    resolved_path: Path | None = None
    with storage.resolve_variant(variant, "vk") as resolved:
        resolved_path = Path(resolved.media[0].source)
        assert resolved_path.exists()
        assert resolved_path.read_bytes() == b"video"
    assert resolved_path is not None
    assert not resolved_path.exists()
