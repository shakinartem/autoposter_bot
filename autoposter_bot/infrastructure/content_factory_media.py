from __future__ import annotations

import hashlib
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlparse

import requests

from autoposter_bot.apps.api.content_factory_contract import MediaRef


class MediaIngestError(RuntimeError):
    pass


class DurableMediaIngestor:
    def __init__(self, cache_dir: Path, *, max_bytes: int = 25 * 1024 * 1024, allowed_hosts: set[str] | None = None) -> None:
        self.cache_dir = cache_dir
        self.max_bytes = max_bytes
        self.allowed_hosts = {host.lower() for host in (allowed_hosts or set()) if host}
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def ingest(self, ref: MediaRef) -> Path:
        parsed = urlparse(str(ref.url))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise MediaIngestError("Content Factory media must use http(s)")
        if self.allowed_hosts and parsed.hostname.lower() not in self.allowed_hosts:
            raise MediaIngestError(f"Media host is not allowlisted: {parsed.hostname}")

        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "-", ref.asset_id)[:160] or "asset"
        extension = self._extension(ref, parsed.path)
        target = self.cache_dir / f"{safe_id}{extension}"
        if target.exists() and 0 < target.stat().st_size <= self.max_bytes:
            return target

        temporary = target.with_suffix(target.suffix + ".part")
        total = 0
        try:
            with requests.get(str(ref.url), stream=True, timeout=(10, 90)) as response:
                response.raise_for_status()
                declared = response.headers.get("content-length")
                if declared and int(declared) > self.max_bytes:
                    raise MediaIngestError("Media exceeds configured byte limit")
                with temporary.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > self.max_bytes:
                            raise MediaIngestError("Media exceeds configured byte limit")
                        handle.write(chunk)
            if total == 0:
                raise MediaIngestError("Media download returned an empty body")
            temporary.replace(target)
            return target
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    @staticmethod
    def source_fingerprint(ref: MediaRef) -> str:
        return hashlib.sha256(str(ref.url).encode("utf-8")).hexdigest()

    @staticmethod
    def _extension(ref: MediaRef, url_path: str) -> str:
        if ref.mime_type:
            guessed = mimetypes.guess_extension(ref.mime_type.split(";")[0].strip())
            if guessed:
                return ".jpg" if guessed == ".jpe" else guessed
        suffix = Path(url_path).suffix.lower()
        if suffix and len(suffix) <= 8:
            return suffix
        return ".bin"
