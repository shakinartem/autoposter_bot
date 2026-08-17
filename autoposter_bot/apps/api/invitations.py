from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from autoposter_bot.application.sessions import WorkspaceSessionService
from autoposter_bot.apps.api.authorization import require_minimum_role, role_level
from autoposter_bot.apps.api.security import AuthContext, get_auth_context
from autoposter_bot.infrastructure.auth_store import AuthStore
from autoposter_bot.infrastructure.invitation_store import INVITABLE_ROLES, InvitationStore


CurrentAuth = Annotated[AuthContext, Depends(get_auth_context)]


class InvitationCreate(BaseModel):
    role: str = Field(default="editor", min_length=4, max_length=16)
    expires_in_hours: int = Field(default=168, ge=1, le=24 * 30)


class InvitationAccept(BaseModel):
    token: str = Field(min_length=20, max_length=512)


def build_invitations_router(
    *,
    invitations: InvitationStore,
    auth_store: AuthStore,
) -> APIRouter:
    router = APIRouter(tags=["invitations"])
    sessions = WorkspaceSessionService(auth_store)

    def require_user_session(auth: AuthContext) -> tuple[int, str]:
        if auth.mode != "session" or auth.user_id is None or not auth.session_id:
            raise HTTPException(status_code=409, detail="User session authentication required")
        return auth.user_id, auth.session_id

    @router.get("/auth/invitations/preview")
    def preview_invitation(token: str = Query(min_length=20, max_length=512)) -> dict[str, Any]:
        invitation = invitations.preview(token)
        if invitation is None:
            raise HTTPException(status_code=404, detail="Invitation is invalid, expired, revoked or already used")
        return {
            "workspace_id": invitation.workspace_id,
            "workspace_name": invitation.workspace_name,
            "role": invitation.role,
            "expires_at": invitation.expires_at.isoformat(),
        }

    @router.get("/api/v1/invitations")
    def list_invitations(auth: CurrentAuth) -> list[dict[str, Any]]:
        require_minimum_role(auth.role, "admin")
        rows = invitations.list_workspace(auth.workspace_id)
        return [
            {
                "id": row["id"],
                "workspace_id": int(row["workspace_id"]),
                "workspace_name": row["workspace_name"],
                "role": row["role"],
                "created_by_user_id": row["created_by_user_id"],
                "created_at": str(row["created_at"]),
                "expires_at": str(row["expires_at"]),
            }
            for row in rows
        ]

    @router.post("/api/v1/invitations", status_code=201)
    def create_invitation(payload: InvitationCreate, auth: CurrentAuth) -> dict[str, Any]:
        require_minimum_role(auth.role, "admin")
        role = payload.role.strip().lower()
        if role not in INVITABLE_ROLES:
            raise HTTPException(status_code=422, detail=f"Unsupported invitation role: {role}")
        if role == "admin" and role_level(auth.role) < role_level("owner"):
            raise HTTPException(status_code=403, detail="Owner role required to invite an admin")
        try:
            token, invitation = invitations.issue(
                workspace_id=auth.workspace_id,
                role=role,
                created_by_user_id=auth.user_id,
                ttl_seconds=payload.expires_in_hours * 60 * 60,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "id": invitation.id,
            "token": token,
            "workspace_id": invitation.workspace_id,
            "workspace_name": invitation.workspace_name,
            "role": invitation.role,
            "expires_at": invitation.expires_at.isoformat(),
        }

    @router.delete("/api/v1/invitations/{invitation_id}", status_code=204)
    def revoke_invitation(invitation_id: str, auth: CurrentAuth) -> None:
        require_minimum_role(auth.role, "admin")
        invitations.revoke(invitation_id, workspace_id=auth.workspace_id)

    @router.post("/api/v1/invitations/accept")
    def accept_invitation(payload: InvitationAccept, auth: CurrentAuth) -> dict[str, Any]:
        user_id, session_id = require_user_session(auth)
        try:
            invitation = invitations.accept(payload.token, user_id=user_id)
            switched = sessions.switch_workspace(
                session_id=session_id,
                user_id=user_id,
                workspace_id=invitation.workspace_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "workspace_id": invitation.workspace_id,
            "workspace_name": invitation.workspace_name,
            "role": switched["role"],
            "session_id": switched["session_id"],
        }

    return router
