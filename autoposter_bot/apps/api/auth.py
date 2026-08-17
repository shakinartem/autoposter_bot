from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from autoposter_bot.apps.api.authorization import require_minimum_role, role_level
from autoposter_bot.apps.api.security import AuthContext, get_auth_context
from autoposter_bot.infrastructure.auth_store import AuthStore, CANONICAL_ROLES


CurrentAuth = Annotated[AuthContext, Depends(get_auth_context)]


class MemberRolePayload(BaseModel):
    role: str = Field(min_length=4, max_length=16)


def build_auth_router(auth_store: AuthStore) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["auth"])

    @router.get("/auth/me")
    def current_identity(auth: CurrentAuth) -> dict[str, Any]:
        return {
            "workspace_id": auth.workspace_id,
            "user_id": auth.user_id,
            "role": auth.role,
            "mode": auth.mode,
            "session_id": auth.session_id,
            "credential_fingerprint": auth.credential_fingerprint,
        }

    @router.get("/members")
    def list_members(auth: CurrentAuth) -> list[dict[str, Any]]:
        require_minimum_role(auth.role, "admin")
        return auth_store.list_members(auth.workspace_id)

    @router.put("/members/{user_id}")
    def set_member_role(
        user_id: int,
        payload: MemberRolePayload,
        auth: CurrentAuth,
    ) -> dict[str, Any]:
        require_minimum_role(auth.role, "admin")
        requested = payload.role.strip().lower()
        if requested not in CANONICAL_ROLES:
            raise HTTPException(status_code=422, detail=f"Unknown workspace role: {requested}")

        # Admins may manage editors/viewers, but only an owner (or trusted service
        # caller) may grant/revoke administrative power.
        if requested in {"owner", "admin"} and role_level(auth.role) < role_level("owner"):
            raise HTTPException(status_code=403, detail="Owner role required to grant admin/owner access")
        if requested == "owner":
            raise HTTPException(
                status_code=409,
                detail="Workspace ownership transfer is a separate operation and cannot be changed through membership role update",
            )

        existing = auth_store.get_membership(auth.workspace_id, user_id)
        if existing and existing["role"] in {"owner", "admin"} and role_level(auth.role) < role_level("owner"):
            raise HTTPException(status_code=403, detail="Owner role required to modify admin access")
        try:
            return auth_store.set_membership(auth.workspace_id, user_id, requested)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.delete("/members/{user_id}", status_code=204)
    def remove_member(user_id: int, auth: CurrentAuth) -> None:
        require_minimum_role(auth.role, "admin")
        existing = auth_store.get_membership(auth.workspace_id, user_id)
        if existing is None:
            return
        if existing["role"] in {"owner", "admin"} and role_level(auth.role) < role_level("owner"):
            raise HTTPException(status_code=403, detail="Owner role required to remove admin access")
        try:
            auth_store.remove_membership(auth.workspace_id, user_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return router
