from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests

from autoposter_bot.integrations.oauth import OAuthIdentity, OAuthProvider, OAuthTokens


class InstagramOAuthProvider(OAuthProvider):
    """Instagram API with Instagram Login provider.

    The account produced by this provider is stored with api_flow=instagram_login,
    which matches the existing InstagramPublisher graph.instagram.com flow.
    """

    platform = "instagram"
    AUTHORIZE_URL = "https://www.instagram.com/oauth/authorize"
    TOKEN_URL = "https://api.instagram.com/oauth/access_token"
    EXCHANGE_URL = "https://graph.instagram.com/access_token"
    REFRESH_URL = "https://graph.instagram.com/refresh_access_token"
    USER_INFO_URL = "https://graph.instagram.com/me"

    def __init__(self) -> None:
        self.client_id = os.getenv("INSTAGRAM_APP_ID", "").strip()
        self.client_secret = os.getenv("INSTAGRAM_APP_SECRET", "").strip()
        self.redirect_uri = os.getenv("INSTAGRAM_REDIRECT_URI", "").strip()
        scopes = [
            item.strip()
            for item in os.getenv(
                "INSTAGRAM_SCOPE",
                "instagram_business_basic,instagram_business_content_publish",
            ).split(",")
            if item.strip()
        ]
        for required in ("instagram_business_basic", "instagram_business_content_publish"):
            if required not in scopes:
                scopes.append(required)
        self.scope = ",".join(dict.fromkeys(scopes))

    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.redirect_uri)

    def authorization_url(self, state: str) -> str:
        if not self.is_configured():
            raise RuntimeError("Instagram OAuth is not configured")
        query = urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "response_type": "code",
                "scope": self.scope,
                "state": state,
                "enable_fb_login": "0",
                "force_authentication": "0",
            }
        )
        return f"{self.AUTHORIZE_URL}?{query}"

    def exchange_code(self, code: str) -> OAuthTokens:
        if not self.is_configured():
            raise RuntimeError("Instagram OAuth is not configured")
        response = requests.post(
            self.TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "authorization_code",
                "redirect_uri": self.redirect_uri,
                "code": code,
            },
            timeout=60,
        )
        payload = response.json()
        if not response.ok or not payload.get("access_token"):
            raise RuntimeError(f"Instagram token exchange failed: {payload}")
        short_token = str(payload["access_token"])
        user_id = payload.get("user_id")

        long_response = requests.get(
            self.EXCHANGE_URL,
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": self.client_secret,
                "access_token": short_token,
            },
            timeout=60,
        )
        long_payload = long_response.json()
        if long_response.ok and long_payload.get("access_token"):
            access_token = str(long_payload["access_token"])
            expires_in = int(long_payload.get("expires_in") or 0)
        else:
            access_token = short_token
            expires_in = int(payload.get("expires_in") or 0)

        now = datetime.now(timezone.utc)
        return OAuthTokens(
            access_token=access_token,
            expires_at=now + timedelta(seconds=expires_in) if expires_in else None,
            scope=self.scope,
            token_type="Bearer",
            metadata={"ig_user_id": user_id},
        )

    def refresh_tokens(self, access_token: str) -> OAuthTokens:
        response = requests.get(
            self.REFRESH_URL,
            params={
                "grant_type": "ig_refresh_token",
                "access_token": access_token,
            },
            timeout=60,
        )
        payload = response.json()
        if not response.ok or not payload.get("access_token"):
            raise RuntimeError(f"Instagram token refresh failed: {payload}")
        expires_in = int(payload.get("expires_in") or 0)
        now = datetime.now(timezone.utc)
        return OAuthTokens(
            access_token=str(payload["access_token"]),
            expires_at=now + timedelta(seconds=expires_in) if expires_in else None,
            scope=self.scope,
            token_type=str(payload.get("token_type") or "Bearer"),
        )

    def fetch_identity(self, tokens: OAuthTokens) -> OAuthIdentity:
        response = requests.get(
            self.USER_INFO_URL,
            params={
                "fields": "user_id,username,name,account_type,profile_picture_url",
                "access_token": tokens.access_token,
            },
            timeout=60,
        )
        payload = response.json()
        if not response.ok:
            raise RuntimeError(f"Instagram user info failed: {payload}")
        external_id = payload.get("user_id") or payload.get("id") or tokens.metadata.get("ig_user_id")
        if not external_id:
            raise RuntimeError(f"Instagram user info did not include an account id: {payload}")
        return OAuthIdentity(
            external_id=str(external_id),
            display_name=str(payload.get("name") or payload.get("username") or "Instagram account"),
            username=str(payload.get("username") or ""),
            metadata={
                "account_type": payload.get("account_type"),
                "profile_picture_url": payload.get("profile_picture_url"),
            },
        )
