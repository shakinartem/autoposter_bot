from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from autoposter_bot.infrastructure.auth_store import AuthStore
from autoposter_bot.infrastructure.identity_store import IdentityStore
from autoposter_bot.infrastructure.login_grant_store import LoginGrantStore
from autoposter_bot.integrations.telegram_oidc import TelegramOIDCProvider


class LoginGrantExchange(BaseModel):
    grant: str = Field(min_length=20, max_length=512)


def build_public_login_router(
    *,
    telegram: TelegramOIDCProvider,
    identities: IdentityStore,
    grants: LoginGrantStore,
    auth_store: AuthStore,
) -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["login"])

    @router.get("/providers")
    def login_providers() -> dict[str, dict[str, Any]]:
        return {
            "telegram": {
                "provider": "telegram",
                "configured": telegram.is_configured(),
            }
        }

    @router.get("/telegram/start")
    def telegram_start(return_path: str = Query(default="/")) -> dict[str, str]:
        if not telegram.is_configured():
            raise HTTPException(status_code=503, detail="Telegram OIDC login is not configured")
        return {"authorization_url": telegram.authorization_url(return_path=return_path)}

    @router.get("/telegram/callback")
    def telegram_callback(
        code: str | None = Query(default=None),
        state: str | None = Query(default=None),
        error: str | None = Query(default=None),
        error_description: str | None = Query(default=None),
    ) -> RedirectResponse:
        if error:
            return _web_redirect("/login", login="error", detail=error_description or error)
        if not code or not state:
            return _web_redirect("/login", login="error", detail="Telegram login callback is incomplete")
        try:
            claims, return_path = telegram.exchange_and_verify(code=code, state_token=state)
            user = identities.find_or_create_telegram_user(claims)
            workspace = identities.ensure_personal_workspace(
                int(user["id"]),
                str(user.get("full_name") or user.get("username") or "Autoposter"),
            )
            grant = grants.issue(
                user_id=int(user["id"]),
                workspace_id=int(workspace["id"]),
                return_path=return_path,
            )
            return _web_redirect("/auth/complete", grant=grant)
        except Exception as exc:
            detail = "Telegram authentication failed" if os.getenv("AUTOPOSTER_ENV", "").strip().lower() in {"prod", "production"} else str(exc)[:300]
            return _web_redirect("/login", login="error", detail=detail)

    @router.post("/login-grant/exchange")
    def exchange_login_grant(payload: LoginGrantExchange) -> dict[str, Any]:
        grant = grants.consume(payload.grant)
        if grant is None:
            raise HTTPException(status_code=401, detail="Login grant is invalid, expired or already consumed")
        try:
            token, identity = auth_store.create_session(
                user_id=grant.user_id,
                workspace_id=grant.workspace_id,
                ttl_seconds=max(
                    300,
                    int(os.getenv("AUTOPOSTER_BROWSER_SESSION_TTL_SECONDS", str(30 * 24 * 60 * 60))),
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return {
            "session_token": token,
            "session_id": identity.session_id,
            "user_id": identity.user_id,
            "workspace_id": identity.workspace_id,
            "role": identity.role,
            "expires_at": identity.expires_at.isoformat(),
            "return_path": grant.return_path,
        }

    return router


def _web_redirect(path: str, **query: str) -> RedirectResponse:
    web_url = os.getenv("AUTOPOSTER_WEB_URL", "http://localhost:3000").rstrip("/")
    safe_path = path if path.startswith("/") and not path.startswith("//") else "/login"
    suffix = f"?{urlencode(query)}" if query else ""
    return RedirectResponse(f"{web_url}{safe_path}{suffix}", status_code=302)
