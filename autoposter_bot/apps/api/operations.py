from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from autoposter_bot.apps.api.authorization import require_minimum_role
from autoposter_bot.apps.api.security import AuthContext, get_auth_context
from autoposter_bot.infrastructure.operations_store import OperationsStore
from autoposter_bot.infrastructure.health_alert_store import HealthAlertStore


CurrentAuth = Annotated[AuthContext, Depends(get_auth_context)]


class ReconciliationResolveRequest(BaseModel):
    action: Literal["confirm_published", "mark_failed", "retry"]
    note: str = Field(min_length=3, max_length=1000)
    external_post_id: str | None = Field(default=None, max_length=500)
    external_url: str | None = Field(default=None, max_length=2000)
    acknowledge_duplicate_risk: bool = False


def build_operations_router(*, operations: OperationsStore, health_alerts: HealthAlertStore | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/v1/operations", tags=["operations"])

    @router.get("/overview")
    def overview(auth: CurrentAuth) -> dict[str, Any]:
        require_minimum_role(auth.role, "admin")
        return operations.workspace_overview(workspace_id=auth.workspace_id)

    @router.get("/alert-state")
    def alert_state(auth: CurrentAuth) -> dict[str, Any] | None:
        require_minimum_role(auth.role, "admin")
        return health_alerts.get_state(auth.workspace_id) if health_alerts is not None else None

    @router.get("/reconciliation")
    def reconciliation_items(
        auth: CurrentAuth,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        require_minimum_role(auth.role, "admin")
        return operations.list_reconciliation_items(workspace_id=auth.workspace_id, limit=limit)

    @router.post("/reconciliation/{publication_id}/resolve")
    def resolve_reconciliation(
        publication_id: str,
        payload: ReconciliationResolveRequest,
        auth: CurrentAuth,
    ) -> dict[str, Any]:
        require_minimum_role(auth.role, "admin")
        try:
            return operations.resolve_publication(
                workspace_id=auth.workspace_id,
                publication_id=publication_id,
                action=payload.action,
                actor_user_id=auth.user_id,
                note=payload.note,
                external_post_id=payload.external_post_id,
                external_url=payload.external_url,
                acknowledge_duplicate_risk=payload.acknowledge_duplicate_risk,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Publication not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/events")
    def events(
        auth: CurrentAuth,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        require_minimum_role(auth.role, "admin")
        return operations.list_events(workspace_id=auth.workspace_id, limit=limit)

    return router
