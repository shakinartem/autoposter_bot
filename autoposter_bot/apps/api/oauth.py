from __future__ import annotations

import os
from datetime import timezone
from typing import Annotated, Any, Callable
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse

from autoposter_bot.apps.api.security import AuthContext, get_auth_context
from autoposter_bot.integrations.oauth import OAuthStateCodec
from autoposter_bot.integrations.tiktok_oauth import TikTokOAuthProvider


CurrentAuth = Annotated[AuthContext, Depends(get_auth_context)]


def build_oauth_router(
    *,
    tiktok: TikTokOAuthProvider,
    store_for_workspace: Callable[[int], Any],
) -> APIRouter:
    router = APIRouter(tags=["oauth"])
    state_codec = OAuthStateCodec.from_env(required=tiktok.is_configured())

    @router.post("/api/v1/oauth/tiktok/start")
    def start_tiktok(
        auth: CurrentAuth,
        return_path: str = Query(default="/accounts"),
    ) -> dict[str, str]:
        if not tiktok.is_configured():
            raise HTTPException(status_code=409, detail="TikTok OAuth is not configured on the server")
        store = store_for_workspace(auth.workspace_id)
        if store.get_workspace() is None:
            raise HTTPException(status_code=403, detail="Workspace is not initialized")
        assert state_codec is not None
        state = state_codec.issue(
            workspace_id=auth.workspace_id,
            platform="tiktok",
            return_path=return_path,
        )
        return {"authorization_url": tiktok.authorization_url(state)}

    @router.get("/api/v1/oauth/tiktok/callback", include_in_schema=True)
    def tiktok_callback(
        code: str | None = Query(default=None),
        state: str | None = Query(default=None),
        error: str | None = Query(default=None),
        error_description: str | None = Query(default=None),
    ) -> RedirectResponse:
        if not tiktok.is_configured() or state_codec is None:
            return _redirect("/accounts", status="error", detail="TikTok OAuth is not configured")
        if not state:
            return _redirect("/accounts", status="error", detail="Missing OAuth state")
        try:
            state_payload = state_codec.verify(state, expected_platform="tiktok")
        except ValueError as exc:
            return _redirect("/accounts", status="error", detail=str(exc))
        return_path = str(state_payload.get("return_path") or "/accounts")
        if error:
            return _redirect(
                return_path,
                status="error",
                detail=error_description or error,
            )
        if not code:
            return _redirect(return_path, status="error", detail="TikTok did not return an authorization code")

        workspace_id = int(state_payload["workspace_id"])
        store = store_for_workspace(workspace_id)
        if store.get_workspace() is None:
            return _redirect(return_path, status="error", detail="Workspace no longer exists")

        try:
            tokens = tiktok.exchange_code(code)
            identity = tiktok.fetch_identity(tokens)
            public_options: dict[str, Any] = {
                "open_id": identity.external_id,
                "scope": tokens.scope,
                "token_type": tokens.token_type,
            }
            if tokens.expires_at:
                public_options["token_expires_at"] = tokens.expires_at.astimezone(timezone.utc).isoformat()
            if tokens.refresh_expires_at:
                public_options["refresh_expires_at"] = tokens.refresh_expires_at.astimezone(timezone.utc).isoformat()
            public_options.update({key: value for key, value in identity.metadata.items() if value is not None})
            options = {
                **public_options,
                "access_token": tokens.access_token,
            }
            if tokens.refresh_token:
                options["refresh_token"] = tokens.refresh_token

            existing = next(
                (
                    account
                    for account in store.list_accounts()
                    if account["platform"].lower() == "tiktok"
                    and str((account.get("options") or {}).get("open_id") or "") == identity.external_id
                ),
                None,
            )
            display_name = identity.display_name or identity.username or "TikTok"
            destination = identity.username or display_name
            if existing:
                store.update_account(
                    int(existing["id"]),
                    name=display_name,
                    destination=destination,
                    options=options,
                )
            else:
                store.create_account(
                    name=display_name,
                    platform="tiktok",
                    destination=destination,
                    options=options,
                )
        except Exception as exc:
            return _redirect(return_path, status="error", detail=str(exc))

        return _redirect(return_path, status="connected", detail="tiktok")

    return router


def _redirect(path: str, *, status: str, detail: str) -> RedirectResponse:
    web_url = os.getenv("AUTOPOSTER_WEB_URL", "http://localhost:3000").rstrip("/")
    safe_path = path if path.startswith("/") and not path.startswith("//") else "/accounts"
    separator = "&" if "?" in safe_path else "?"
    target = f"{web_url}{safe_path}{separator}{urlencode({'oauth': status, 'detail': detail[:300]})}"
    return RedirectResponse(target, status_code=302)
