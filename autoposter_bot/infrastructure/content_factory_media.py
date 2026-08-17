from __future__ import annotations

import mimetypes
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests

from autoposter_bot.apps.api.content_factory_contract import MediaRef
from autoposter_bot.domain.content import MediaAsset
from autoposter_bot.infrastructure.content_factory_ledger import ContentFactoryLedger
from autoposter_bot.infrastructure.media_storage import MediaStorage


class MediaIngestError(RuntimeError):
    pass


class DurableMediaIngestor:
    def __init__(self, storage: MediaStorage, ledger: ContentFactoryLedger, *, max_bytes: int = 25 * 1024 * 1024, allowed_hosts: set[str] | None = None) -> None:
        self.storage = storage
        self.ledger = ledger
        self.max_bytes = max_bytes
        self.allowed_hosts = {host.lower() for host in (allowed_hosts or set()) if host}

    def ingest(self, ref: MediaRef, *, workspace_id: int) -> MediaAsset:
        existing = self.ledger.get_media_link(ref.asset_id, workspace_id)
        if existing:
            return MediaAsset(id=f"cf-{ref.asset_id}", source=existing["source"], media_type=existing["media_type"], metadata=existing.get("metadata_json") or {})
        parsed = urlparse(str(ref.url))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise MediaIngestError("Content Factory media must use http(s)")
        if self.allowed_hosts and parsed.hostname.lower() not in self.allowed_hosts:
            raise MediaIngestError(f"Media host is not allowlisted: {parsed.hostname}")
        filename = self._filename(ref, parsed.path)
        total = 0
        with tempfile.SpooledTemporaryFile(max_size=min(self.max_bytes, 8 * 1024 * 1024)) as handle:
            try:
                with requests.get(str(ref.url), stream=True, timeout=(10, 90)) as response:
                    response.raise_for_status()
                    declared = response.headers.get("content-length")
                    if declared and int(declared) > self.max_bytes:
                        raise MediaIngestError("Media exceeds configured byte limit")
                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        if not chunk: continue
                        total += len(chunk)
                        if total > self.max_bytes: raise MediaIngestError("Media exceeds configured byte limit")
                        handle.write(chunk)
                if total == 0: raise MediaIngestError("Media download returned an empty body")
                handle.seek(0)
                stored = self.storage.put(workspace_id=workspace_id, filename=filename, fileobj=handle, content_type=ref.mime_type)
            except MediaIngestError:
                raise
            except Exception as exc:
                raise MediaIngestError(str(exc)) from exc
        metadata = {"content_factory_asset_id": ref.asset_id, "mime_type": ref.mime_type or stored.content_type, "width": ref.width, "height": ref.height, "storage_key": stored.storage_key, "original_name": stored.original_name}
        self.ledger.save_media_link(asset_id=ref.asset_id, workspace_id=workspace_id, source=stored.source, media_type=stored.media_type, metadata=metadata)
        return MediaAsset(id=f"cf-{ref.asset_id}", source=stored.source, media_type=stored.media_type, metadata=metadata)

    @staticmethod
    def _filename(ref: MediaRef, url_path: str) -> str:
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "-", ref.asset_id)[:120] or "asset"
        suffix = Path(url_path).suffix.lower()
        if not suffix or len(suffix) > 8:
            guessed = mimetypes.guess_extension((ref.mime_type or "").split(";")[0].strip()) if ref.mime_type else None
            suffix = ".jpg" if guessed == ".jpe" else (guessed or ".bin")
        return f"{safe_id}{suffix}"
