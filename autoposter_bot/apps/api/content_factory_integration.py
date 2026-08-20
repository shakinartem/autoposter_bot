from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException

from autoposter_bot.application.content_factory_bridge import ContentFactoryBridge, IdempotencyConflict
from autoposter_bot.application.content_factory_feedback import ContentFactoryFeedback
from autoposter_bot.apps.api.content_factory_contract import ContentPackageV1
from autoposter_bot.infrastructure.content_factory_ledger import ContentFactoryLedger
from autoposter_bot.infrastructure.content_factory_media import DurableMediaIngestor, MediaIngestError


class ContentFactoryIntegration:
    def __init__(self, *, persistence: Any, media_storage: Any, registry: Any) -> None:
        self.persistence = persistence
        self.ledger = ContentFactoryLedger(persistence)
        default_workspace_raw = os.getenv("CONTENT_FACTORY_DEFAULT_WORKSPACE_ID", "").strip()
        default_workspace_id = int(default_workspace_raw) if default_workspace_raw else None
        max_bytes = int(os.getenv("CONTENT_FACTORY_MEDIA_MAX_BYTES", str(25 * 1024 * 1024)))
        allowed_hosts = {host.strip().lower() for host in os.getenv("CONTENT_FACTORY_MEDIA_ALLOWED_HOSTS", "").split(",") if host.strip()}
        allow_private = os.getenv("CONTENT_FACTORY_MEDIA_ALLOW_PRIVATE_HOSTS", "false").strip().lower() in {"1", "true", "yes", "on"}
        require_allowlist = os.getenv("CONTENT_FACTORY_MEDIA_REQUIRE_ALLOWLIST", "false").strip().lower() in {"1", "true", "yes", "on"}
        if require_allowlist and not allowed_hosts:
            raise RuntimeError("CONTENT_FACTORY_MEDIA_ALLOWED_HOSTS is required when media allowlisting is enforced")
        self.bridge = ContentFactoryBridge(
            persistence,
            self.ledger,
            DurableMediaIngestor(
                media_storage,
                self.ledger,
                max_bytes=max_bytes,
                allowed_hosts=allowed_hosts,
                allow_private_hosts=allow_private,
            ),
            default_workspace_id=default_workspace_id,
        )
        self.feedback = ContentFactoryFeedback(
            self.ledger,
            endpoint=os.getenv("CONTENT_FACTORY_PERFORMANCE_URL", ""),
            token=os.getenv("CONTENT_FACTORY_PERFORMANCE_TOKEN", ""),
        )
        self.registry = registry
        self.ingest_token = os.getenv("CONTENT_FACTORY_INGEST_TOKEN", "")
        self.router = APIRouter(prefix="/api/v1/integrations/content-factory", tags=["content-factory"])
        self._mount_routes()

    def init_schema(self) -> None:
        self.ledger.init_schema()

    def _authorize(self, authorization: str | None) -> None:
        if not self.ingest_token:
            raise HTTPException(503, "Content Factory ingestion is disabled until CONTENT_FACTORY_INGEST_TOKEN is configured")
        expected = f"Bearer {self.ingest_token}"
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise HTTPException(401, "Invalid Content Factory token")

    def _mount_routes(self) -> None:
        @self.router.post("/packages", status_code=202)
        def ingest_package(
            package: ContentPackageV1,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
            authorization: str | None = Header(default=None, alias="Authorization"),
        ):
            self._authorize(authorization)
            if not idempotency_key or len(idempotency_key) > 255:
                raise HTTPException(400, "A valid Idempotency-Key header is required")
            try:
                return self.bridge.ingest(
                    package,
                    idempotency_key=idempotency_key,
                    supported_platforms=set(self.registry.capabilities()),
                )
            except IdempotencyConflict as exc:
                raise HTTPException(409, str(exc)) from exc
            except MediaIngestError as exc:
                raise HTTPException(422, f"Media ingestion failed: {exc}") from exc
            except RuntimeError as exc:
                raise HTTPException(409, str(exc)) from exc

        @self.router.post("/feedback/dispatch")
        def dispatch_feedback(authorization: str | None = Header(default=None, alias="Authorization")):
            self._authorize(authorization)
            harvest = self.feedback.harvest(limit=1000)
            delivery = self.feedback.dispatch(limit=250)
            return {"harvest": harvest, "delivery": delivery}
