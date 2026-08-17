from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException

from autoposter_bot.application.content_factory_bridge import ContentFactoryBridge, IdempotencyConflict
from autoposter_bot.application.content_factory_feedback import ContentFactoryFeedback, FeedbackNotLinked
from autoposter_bot.apps.api.content_factory_contract import ContentPackageV1, PerformanceSnapshotRequest
from autoposter_bot.infrastructure.content_factory_ledger import ContentFactoryLedger
from autoposter_bot.infrastructure.content_factory_media import DurableMediaIngestor, MediaIngestError


class ContentFactoryIntegration:
    def __init__(self, *, persistence: Any, media_storage: Any, registry: Any) -> None:
        self.persistence = persistence
        self.ledger = ContentFactoryLedger(persistence)
        default_workspace_raw = os.getenv("CONTENT_FACTORY_DEFAULT_WORKSPACE_ID", "").strip()
        default_workspace_id = int(default_workspace_raw) if default_workspace_raw else None
        max_bytes = int(os.getenv("CONTENT_FACTORY_MEDIA_MAX_BYTES", str(25 * 1024 * 1024)))
        allowed_hosts = {
            host.strip().lower()
            for host in os.getenv("CONTENT_FACTORY_MEDIA_ALLOWED_HOSTS", "").split(",")
            if host.strip()
        }
        self.bridge = ContentFactoryBridge(
            persistence,
            self.ledger,
            DurableMediaIngestor(media_storage, self.ledger, max_bytes=max_bytes, allowed_hosts=allowed_hosts),
            default_workspace_id=default_workspace_id,
        )
        self.feedback = ContentFactoryFeedback(
            self.ledger,
            endpoint=os.getenv("CONTENT_FACTORY_PERFORMANCE_URL", ""),
            token=os.getenv("CONTENT_FACTORY_PERFORMANCE_TOKEN", ""),
        )
        self.registry = registry
        self.ingest_token = os.getenv("CONTENT_FACTORY_INGEST_TOKEN", "")
        self.analytics_token = os.getenv("AUTOPOSTER_ANALYTICS_INGEST_TOKEN", "")
        self.router = APIRouter(prefix="/api/v1/integrations/content-factory", tags=["content-factory"])
        self._mount_routes()

    def init_schema(self) -> None:
        self.ledger.init_schema()

    def _authorize_package(self, authorization: str | None) -> None:
        if not self.ingest_token:
            raise HTTPException(503, "Content Factory ingestion is disabled until CONTENT_FACTORY_INGEST_TOKEN is configured")
        expected = f"Bearer {self.ingest_token}"
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise HTTPException(401, "Invalid Content Factory token")

    def _authorize_analytics(self, token: str | None) -> None:
        if not self.analytics_token:
            raise HTTPException(503, "Analytics ingestion is disabled until AUTOPOSTER_ANALYTICS_INGEST_TOKEN is configured")
        if not token or not hmac.compare_digest(token, self.analytics_token):
            raise HTTPException(401, "Invalid analytics token")

    def _mount_routes(self) -> None:
        @self.router.post("/packages", status_code=202)
        def ingest_package(
            package: ContentPackageV1,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
            authorization: str | None = Header(default=None, alias="Authorization"),
        ):
            self._authorize_package(authorization)
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

        @self.router.post("/publications/{publication_id}/performance", status_code=202)
        def record_performance(
            publication_id: str,
            payload: PerformanceSnapshotRequest,
            analytics_token: str | None = Header(default=None, alias="X-Analytics-Token"),
        ):
            self._authorize_analytics(analytics_token)
            try:
                result = self.feedback.record_snapshot(
                    publication_id,
                    metrics=payload.metrics,
                    captured_at=payload.captured_at,
                    event_id=payload.event_id,
                    metadata=payload.metadata,
                )
                result["dispatch"] = self.feedback.dispatch(limit=10)
                return result
            except FeedbackNotLinked as exc:
                raise HTTPException(409, str(exc)) from exc
            except KeyError as exc:
                raise HTTPException(404, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc

        @self.router.post("/performance/dispatch")
        def dispatch_performance(
            analytics_token: str | None = Header(default=None, alias="X-Analytics-Token"),
        ):
            self._authorize_analytics(analytics_token)
            return self.feedback.dispatch(limit=100)
