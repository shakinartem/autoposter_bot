from __future__ import annotations

import os
from datetime import timezone
from typing import Annotated, Any, Callable
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse

from autoposter_bot.apps.api.authorization import require_minimum_role
from autoposter_bot.apps.api.security import AuthContext, get_auth_context
from autoposter_bot.integrations.oauth import OAuthIdentity, OAuthProvider, OAuthStateCodec, OAuthTokens


CurrentAuth = Annotated[AuthContext, Depends(get_auth_context)]


def build_oauth_router(
    *,
    providers: dict[str, OAuthProvider],
    store_for_workspace: Callable[[int], Any],
) -> APIRouter:
    """Workspace-bound OAuth connection endpoints.

    Starting a connection is an administrative action because a successful
    callback creates or mutates encrypted publishing credentials. The callback
    itself cannot require the browser session bearer because third-party OAuth
    providers redirect to it directly; encrypted short-lived state binds it to
    the initiating workspace and platform.
    """

    router = APIRouter(prefix="/api/v1/oauth", tags=["oauth"])
    normalized = {key.strip().lower(): value for key, value in providers.items()}
    state_codec = OAuthStateCodec.from_env(
        required=any(provider.is_configured() for provider in normalized.values())
    )

    @router.get("/providers")
    def oauth_providers(auth: CurrentAuth) -> dict[str, dict[str, Any]]:
        store = store_for_workspace(auth.workspace_id)
        if store.get_workspace() is None:
            raise HTTPException(status_code=403, detail="Workspace is not initialized")
        return {
            platform: {
                "platform": platform,
                "configured": provider.is_configured(),
            }
            for platform, provider in normalized.items()
        }

    @router.post("/{platform}/start")
    def start_oauth(
        platform: str,
        auth: CurrentAuth,
        return_path: str = Query(default="/accounts"),
    ) -> dict[str, str]:
        require_minimum_role(auth.role, "admin")
        key = platform.strip().lower()
        provider = normalized.get(key)
        if provider is None:
            raise HTTPException(status_code=404, detail=f"OAuth provider is not supported: {key}")
        if not provider.is_configured():
            raise HTTPException(status_code=409, detail=f"{key} OAuth is not configured on the server")

        store = store_for_workspace(auth.workspace_id)
        if store.get_workspace() is None:
            raise HTTPException(status_code=403, detail="Workspace is not initialized")
        if state_codec is None:
            raise HTTPException(status_code=503, detail="OAuth state encryption is not configured")

        state = state_codec.issue(
            workspace_id=auth.workspace_id,
            platform=key,
            return_path=return_path,
        )
        return {"authorization_url": provider.authorization_url(state)}

    @router.get("/{platform}/callback", include_in_schema=True)
    def oauth_callback(
        platform: str,
        code: str | None = Query(default=None),
        state: str | None = Query(default=None),
        error: str | None = Query(default=None),
        error_description: str | None = Query(default=None),
    ) -> RedirectResponse:
        key = platform.strip().lower()
        provider = normalized.get(key)
        if provider is None:
            return _redirect("/accounts", status="error", detail=f"Unsupported OAuth provider: {key}")
        if not provider.is_configured() or state_codec is None:
            return _redirect("/accounts", status="error", detail=f"{key} OAuth is not configured")
        if not state:
            return _redirect("/accounts", status="error", detail="Missing OAuth state")

        try:
            state_payload = state_codec.verify(state, expected_platform=key)
        except ValueError as exc:
            return _redirect("/accounts", status="error", detail=str(exc))

        return_path = str(state_payload.get("return_path") or "/accounts")
        if error:
            return _redirect(return_path, status="error", detail=error_description or error)
        if not code:
            return _redirect(return_path, status="error", detail=f"{key} did not return an authorization code")

        workspace_id = int(state_payload["workspace_id"])
        store = store_for_workspace(workspace_id)
        if store.get_workspace() is None:
            return _redirect(return_path, status="error", detail="Workspace no longer exists")

        try:
            tokens = provider.exchange_code(code)
            identity = provider.fetch_identity(tokens)
            options = _build_account_options(key, tokens, identity)
            existing = _find_existing_account(store.list_accounts(), key, identity.external_id)
            display_name = identity.display_name or identity.username or key.title()
            destination = f"@{identity.username.lstrip('@')}" if identity.username else display_name
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
                    platform=key,
                    destination=destination,
                    options=options,
                )
        except Exception as exc:
            return _redirect(return_path, status="error", detail=str(exc))

        return _redirect(return_path, status="connected", detail=key)

    return router


def _build_account_options(
    platform: str,
    tokens: OAuthTokens,
    identity: OAuthIdentity,
) -> dict[str, Any]:
    options: dict[str, Any] = {
        "oauth_provider": platform,
        "external_id": identity.external_id,
        "scope": tokens.scope,
        "token_type": tokens.token_type,
        "access_token": tokens.access_token,
    }
    if tokens.refresh_token:
        options["refresh_token"] = tokens.refresh_token
    if tokens.expires_at:
        options["token_expires_at"] = tokens.expires_at.astimezone(timezone.utc).isoformat()
    if tokens.refresh_expires_at:
        options["refresh_expires_at"] = tokens.refresh_expires_at.astimezone(timezone.utc).isoformat()

    if platform == "tiktok":
        options["open_id"] = identity.external_id
    elif platform == "instagram":
        options["ig_user_id"] = identity.external_id
        options["api_flow"] = "instagram_login"

    options.update({key: value for key, value in tokens.metadata.items() if value is not None})
    options.update({key: value for key, value in identity.metadata.items() if value is not None})
    return options


def _find_existing_account(
    accounts: list[dict[str, Any]],
    platform: str,
    external_id: str,
) -> dict[str, Any] | None:
    identity_keys = ("external_id", "open_id", "ig_user_id")
    for account in accounts:
        if str(account.get("platform") or "").lower() != platform:
            continue
        options = account.get("options") or {}
        if any(str(options.get(key) or "") == external_id for key in identity_keys):
            return account
    return None


def _redirect(path: str, *, status: str, detail: str) -> RedirectResponse:
    web_url = os.getenv("AUTOPOSTER_WEB_URL", "http://localhost:3000").rstrip("/")
    safe_path = path if path.startswith("/") and not path.startswith("//") else "/accounts"
    separator = "&" if "?" in safe_path else "?"
    target = f"{web_url}{safe_path}{separator}{urlencode({'oauth': status, 'detail': detail[:300]})}"
    return RedirectResponse(target, status_code=302)
