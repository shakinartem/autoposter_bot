from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from autoposter_bot.apps.api.authorization import require_minimum_role
from autoposter_bot.apps.api.security import AuthContext, get_auth_context
from autoposter_bot.infrastructure.operations_store import OperationsStore


CurrentAuth = Annotated[AuthContext, Depends(get_auth_context)]


def build_operations_router(*, operations: OperationsStore) -> APIRouter:
    router = APIRouter(prefix="/api/v1/operations", tags=["operations"])

    @router.get("/overview")
    def overview(auth: CurrentAuth) -> dict[str, Any]:
        require_minimum_role(auth.role, "admin")
        return operations.workspace_overview(workspace_id=auth.workspace_id)

    return router
