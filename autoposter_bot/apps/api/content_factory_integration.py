from __future__ import annotations

import hmac
import os
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException

from autoposter_bot.application.content_factory_bridge import ContentFactoryBridge, IdempotencyConflict
from autoposter_bot.apps.api.content_factory_contract import ContentPackageV1
from autoposter_bot.config import load_settings
from autoposter_bot.infrastructure.content_factory_media import DurableMediaIngestor, MediaIngestError
from autoposter_bot.infrastructure.content_store import SQLiteContentStore
from autoposter_bot.platforms.factory import build_default_platform_registry

router = APIRouter(prefix="/api/v1/integrations/content-factory", tags=["content-factory"])
settings = load_settings()
store = SQLiteContentStore(settings.database_path)
registry = build_default_platform_registry(settings)

TOKEN = os.getenv("CONTENT_FACTORY_INGEST_TOKEN", "")
MEDIA_CACHE_DIR = Path(os.getenv("CONTENT_FACTORY_MEDIA_CACHE_DIR", "data/content_factory_media"))
MEDIA_MAX_BYTES = int(os.getenv("CONTENT_FACTORY_MEDIA_MAX_BYTES", str(25 * 1024 * 1024)))
ALLOWED_HOSTS = {host.strip().lower() for host in os.getenv("CONTENT_FACTORY_MEDIA_ALLOWED_HOSTS", "").split(",") if host.strip()}
bridge = ContentFactoryBridge(
    store,
    DurableMediaIngestor(MEDIA_CACHE_DIR, max_bytes=MEDIA_MAX_BYTES, allowed_hosts=ALLOWED_HOSTS),
)


def init_content_factory_bridge() -> None:
    bridge.init_schema()


def _authorize(authorization: str | None) -> None:
    if not TOKEN:
        raise HTTPException(503, "Content Factory ingestion is disabled until CONTENT_FACTORY_INGEST_TOKEN is configured")
    expected = f"Bearer {TOKEN}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(401, "Invalid Content Factory token")


@router.post("/packages", status_code=202)
def ingest_package(
    package: ContentPackageV1,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    _authorize(authorization)
    if not idempotency_key or len(idempotency_key) > 255:
        raise HTTPException(400, "A valid Idempotency-Key header is required")
    try:
        return bridge.ingest(package, idempotency_key=idempotency_key, supported_platforms=set(registry.capabilities()))
    except IdempotencyConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except MediaIngestError as exc:
        raise HTTPException(422, f"Media ingestion failed: {exc}") from exc
    except Exception as exc:
        raise HTTPException(502, f"Content Factory package ingestion failed: {exc}") from exc
