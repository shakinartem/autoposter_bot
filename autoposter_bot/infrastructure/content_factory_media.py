from __future__ import annotations

import ipaddress
import mimetypes
import re
import socket
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
    """Copies Factory media into Autoposter-owned storage.

    Production is HTTPS/public-address only. Local HTTP/private addresses are accepted
    only when CONTENT_FACTORY_MEDIA_ALLOW_PRIVATE_HOSTS=true is deliberately enabled.
    Redirects are rejected so the validated host cannot be swapped after validation.
    """

    def __init__(
        self,
        storage: MediaStorage,
        ledger: ContentFactoryLedger,
        *,
        max_bytes: int = 25 * 1024 * 1024,
        allowed_hosts: set[str] | None = None,
        allow_private_hosts: bool = False,
    ) -> None:
        self.storage = storage
        self.ledger = ledger
        self.max_bytes = max_bytes
        self.allowed_hosts = {host.lower() for host in (allowed_hosts or set()) if host}
        self.allow_private_hosts = allow_private_hosts

    def ingest(self, ref: MediaRef, *, workspace_id: int) -> MediaAsset:
        existing = self.ledger.get_media_link(ref.asset_id, workspace_id)
        if existing:
            return MediaAsset(
                id=f"cf-{ref.asset_id}",
                source=existing["source"],
                media_type=existing["media_type"],
                metadata=existing.get("metadata_json") or {},
            )

        parsed = self._validate_url(str(ref.url))
        filename = self._filename(ref, parsed.path)
        total = 0
        with tempfile.SpooledTemporaryFile(max_size=min(self.max_bytes, 8 * 1024 * 1024)) as handle:
            try:
                with requests.get(str(ref.url), stream=True, timeout=(10, 90), allow_redirects=False) as response:
                    if 300 <= response.status_code < 400:
                        raise MediaIngestError("Media redirects are disabled; send the final signed URL")
                    response.raise_for_status()
                    response_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
                    expected_type = (ref.mime_type or "").split(";", 1)[0].strip().lower()
                    if ref.type == "image" and response_type and not response_type.startswith("image/"):
                        raise MediaIngestError(f"Expected image media but upstream returned {response_type}")
                    if expected_type and response_type and expected_type.split("/", 1)[0] != response_type.split("/", 1)[0]:
                        raise MediaIngestError(f"Media content-type mismatch: expected {expected_type}, got {response_type}")
                    declared = response.headers.get("content-length")
                    if declared and int(declared) > self.max_bytes:
                        raise MediaIngestError("Media exceeds configured byte limit")
                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > self.max_bytes:
                            raise MediaIngestError("Media exceeds configured byte limit")
                        handle.write(chunk)
                if total == 0:
                    raise MediaIngestError("Media download returned an empty body")
                handle.seek(0)
                self._validate_image_signature(handle, ref.mime_type or response.headers.get("content-type"))
                handle.seek(0)
                stored = self.storage.put(
                    workspace_id=workspace_id,
                    filename=filename,
                    fileobj=handle,
                    content_type=ref.mime_type,
                )
            except MediaIngestError:
                raise
            except Exception as exc:
                raise MediaIngestError(str(exc)) from exc

        metadata = {
            "content_factory_asset_id": ref.asset_id,
            "mime_type": ref.mime_type or stored.content_type,
            "width": ref.width,
            "height": ref.height,
            "storage_key": stored.storage_key,
            "original_name": stored.original_name,
        }
        self.ledger.save_media_link(
            asset_id=ref.asset_id,
            workspace_id=workspace_id,
            source=stored.source,
            media_type=stored.media_type,
            metadata=metadata,
        )
        return MediaAsset(
            id=f"cf-{ref.asset_id}",
            source=stored.source,
            media_type=stored.media_type,
            metadata=metadata,
        )


    @staticmethod
    def _validate_image_signature(handle, declared_mime: str | None) -> None:
        position = handle.tell()
        handle.seek(0)
        head = handle.read(32)
        handle.seek(position)
        mime = (declared_mime or "").split(";", 1)[0].strip().lower()
        if not mime.startswith("image/"):
            return
        signatures = {
            "image/png": head.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/jpeg": head.startswith(b"\xff\xd8\xff"),
            "image/jpg": head.startswith(b"\xff\xd8\xff"),
            "image/gif": head.startswith((b"GIF87a", b"GIF89a")),
            "image/webp": len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP",
        }
        if mime in signatures and not signatures[mime]:
            raise MediaIngestError(f"Downloaded bytes do not match declared image type {mime}")

    def _validate_url(self, raw_url: str):
        parsed = urlparse(raw_url)
        allowed_schemes = {"https"} | ({"http"} if self.allow_private_hosts else set())
        if parsed.scheme not in allowed_schemes or not parsed.hostname:
            requirement = "http(s)" if self.allow_private_hosts else "https"
            raise MediaIngestError(f"Content Factory media must use {requirement}")
        hostname = parsed.hostname.lower()
        if self.allowed_hosts and hostname not in self.allowed_hosts:
            raise MediaIngestError(f"Media host is not allowlisted: {hostname}")
        if not self.allow_private_hosts:
            try:
                addresses = {
                    entry[4][0]
                    for entry in socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
                }
            except socket.gaierror as exc:
                raise MediaIngestError(f"Unable to resolve media host: {hostname}") from exc
            for raw_address in addresses:
                ip = ipaddress.ip_address(raw_address)
                if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_multicast
                    or ip.is_reserved
                    or ip.is_unspecified
                ):
                    raise MediaIngestError(f"Private or unsafe media address is blocked: {hostname}")
        return parsed

    @staticmethod
    def _filename(ref: MediaRef, url_path: str) -> str:
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "-", ref.asset_id)[:120] or "asset"
        suffix = Path(url_path).suffix.lower()
        if not suffix or len(suffix) > 8:
            guessed = (
                mimetypes.guess_extension((ref.mime_type or "").split(";")[0].strip())
                if ref.mime_type
                else None
            )
            suffix = ".jpg" if guessed == ".jpe" else (guessed or ".bin")
        return f"{safe_id}{suffix}"
