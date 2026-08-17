from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from autoposter_bot.integrations.tiktok_oauth import TikTokOAuthProvider


class CredentialRefreshService:
    def __init__(self, *, tiktok: TikTokOAuthProvider, refresh_before_seconds: int = 300) -> None:
        self.tiktok = tiktok
        self.refresh_before = timedelta(seconds=max(30, refresh_before_seconds))

    def refresh_if_needed(
        self,
        platform: str,
        options: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        if platform.lower() != "tiktok":
            return dict(options), False
        return self._refresh_tiktok(options)

    def _refresh_tiktok(self, options: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        current = dict(options)
        expires_at = self._parse_datetime(current.get("token_expires_at"))
        if expires_at is None:
            return current, False
        now = datetime.now(timezone.utc)
        if expires_at > now + self.refresh_before:
            return current, False
        refresh_token = str(current.get("refresh_token") or "")
        if not refresh_token:
            raise RuntimeError("TikTok access token is expiring and no refresh token is stored; reconnect the account")
        tokens = self.tiktok.refresh_tokens(refresh_token)
        current["access_token"] = tokens.access_token
        if tokens.refresh_token:
            current["refresh_token"] = tokens.refresh_token
        if tokens.expires_at:
            current["token_expires_at"] = tokens.expires_at.astimezone(timezone.utc).isoformat()
        if tokens.refresh_expires_at:
            current["refresh_expires_at"] = tokens.refresh_expires_at.astimezone(timezone.utc).isoformat()
        if tokens.scope:
            current["scope"] = tokens.scope
        if tokens.token_type:
            current["token_type"] = tokens.token_type
        open_id = tokens.metadata.get("open_id")
        if open_id:
            current["open_id"] = open_id
        return current, True

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            parsed = value
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
