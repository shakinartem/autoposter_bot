from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from autoposter_bot.application.account_health import AccountHealthApplication
from autoposter_bot.apps.api.authorization import require_minimum_role
from autoposter_bot.apps.api.security import AuthContext, get_auth_context
from autoposter_bot.infrastructure.account_health_store import AccountHealthStore


CurrentAuth = Annotated[AuthContext, Depends(get_auth_context)]


def build_account_health_router(
    *,
    application: AccountHealthApplication,
    account_health: AccountHealthStore,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["account-health"])

    @router.get("/operations/account-health")
    def list_account_health(auth: CurrentAuth) -> list[dict[str, Any]]:
        require_minimum_role(auth.role, "admin")
        return account_health.list_workspace(workspace_id=auth.workspace_id)

    @router.post("/operations/account-health/probe")
    def probe_workspace(auth: CurrentAuth) -> list[dict[str, Any]]:
        require_minimum_role(auth.role, "admin")
        return application.probe_workspace(auth.workspace_id)

    @router.post("/accounts/{account_id}/probe")
    def probe_account(account_id: int, auth: CurrentAuth) -> dict[str, Any]:
        require_minimum_role(auth.role, "admin")
        try:
            return application.probe_account(auth.workspace_id, account_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Social account not found") from exc

    return router
