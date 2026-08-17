from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests

from autoposter_bot.config import Settings
from autoposter_bot.integrations.oauth import OAuthIdentity, OAuthProvider, OAuthTokens


class TikTokOAuthProvider(OAuthProvider):
    platform = "tiktok"
    AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
    TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
    USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"

    def __init__(self, settings: Settings) -> None:
        self.client_key = settings.tiktok_client_key or ""
        self.client_secret = settings.tiktok_client_secret or ""
        self.redirect_uri = settings.tiktok_redirect_uri or ""
        scopes = [item.strip() for item in (settings.tiktok_scope or "").split(",") if item.strip()]
        if "user.info.basic" not in scopes:
            scopes.insert(0, "user.info.basic")
        self.scope = ",".join(dict.fromkeys(scopes))

    def is_configured(self) -> bool:
        return bool(self.client_key and self.client_secret and self.redirect_uri)

    def authorization_url(self, state: str) -> str:
        if not self.is_configured():
            raise RuntimeError("TikTok OAuth is not configured")
        return f"{self.AUTHORIZE_URL}?{urlencode({
            'client_key': self.client_key,
            'response_type': 'code',
            'scope': self.scope,
            'redirect_uri': self.redirect_uri,
            'state': state,
        })}"

    def exchange_code(self, code: str) -> OAuthTokens:
        if not self.is_configured():
            raise RuntimeError("TikTok OAuth is not configured")
        response = requests.post(
            self.TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_key": self.client_key,
                "client_secret": self.client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": self.redirect_uri,
            },
            timeout=60,
        )
        payload = response.json()
        if not response.ok or not payload.get("access_token"):
            raise RuntimeError(f"TikTok token exchange failed: {payload}")
        now = datetime.now(timezone.utc)
        expires_in = int(payload.get("expires_in") or 0)
        refresh_expires_in = int(payload.get("refresh_expires_in") or 0)
        return OAuthTokens(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token"),
            expires_at=now + timedelta(seconds=expires_in) if expires_in else None,
            refresh_expires_at=(
                now + timedelta(seconds=refresh_expires_in) if refresh_expires_in else None
            ),
            scope=str(payload.get("scope") or ""),
            token_type=str(payload.get("token_type") or "Bearer"),
            metadata={"open_id": payload.get("open_id")},
        )

    def fetch_identity(self, tokens: OAuthTokens) -> OAuthIdentity:
        response = requests.get(
            self.USER_INFO_URL,
            headers={"Authorization": f"Bearer {tokens.access_token}"},
            params={"fields": "open_id,union_id,avatar_url,display_name"},
            timeout=60,
        )
        payload = response.json()
        user = (payload.get("data") or {}).get("user") or {}
        error = payload.get("error") or {}
        if not response.ok or error.get("code") not in {None, "", "ok"} or not user.get("open_id"):
            fallback_open_id = tokens.metadata.get("open_id")
            if fallback_open_id:
                return OAuthIdentity(
                    external_id=str(fallback_open_id),
                    display_name="TikTok account",
                )
            raise RuntimeError(f"TikTok user info failed: {payload}")
        return OAuthIdentity(
            external_id=str(user["open_id"]),
            display_name=str(user.get("display_name") or "TikTok account"),
            username=str(user.get("display_name") or ""),
            metadata={
                "union_id": user.get("union_id"),
                "avatar_url": user.get("avatar_url"),
            },
        )
