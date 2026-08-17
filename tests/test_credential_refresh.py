from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from autoposter_bot.integrations.credential_refresh import CredentialRefreshService
from autoposter_bot.integrations.oauth import OAuthTokens


class FakeTikTokProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def refresh_tokens(self, refresh_token: str) -> OAuthTokens:
        self.calls.append(refresh_token)
        now = datetime.now(timezone.utc)
        return OAuthTokens(
            access_token="new-access-token",
            refresh_token="new-refresh-token",
            expires_at=now + timedelta(hours=24),
            refresh_expires_at=now + timedelta(days=180),
            scope="user.info.basic,video.publish",
            token_type="Bearer",
            metadata={"open_id": "creator-123"},
        )


def test_tiktok_refreshes_when_access_token_is_about_to_expire():
    provider = FakeTikTokProvider()
    service = CredentialRefreshService(tiktok=provider, refresh_before_seconds=300)
    now = datetime.now(timezone.utc)

    options, changed = service.refresh_if_needed(
        "tiktok",
        {
            "access_token": "old-access-token",
            "refresh_token": "old-refresh-token",
            "token_expires_at": (now + timedelta(seconds=60)).isoformat(),
            "open_id": "creator-123",
        },
    )

    assert changed is True
    assert provider.calls == ["old-refresh-token"]
    assert options["access_token"] == "new-access-token"
    assert options["refresh_token"] == "new-refresh-token"
    assert options["open_id"] == "creator-123"
    assert options["scope"] == "user.info.basic,video.publish"


def test_tiktok_does_not_refresh_healthy_token():
    provider = FakeTikTokProvider()
    service = CredentialRefreshService(tiktok=provider, refresh_before_seconds=300)
    now = datetime.now(timezone.utc)

    options, changed = service.refresh_if_needed(
        "tiktok",
        {
            "access_token": "healthy",
            "refresh_token": "refresh",
            "token_expires_at": (now + timedelta(hours=2)).isoformat(),
        },
    )

    assert changed is False
    assert provider.calls == []
    assert options["access_token"] == "healthy"


def test_expiring_tiktok_token_without_refresh_token_requires_reconnect():
    provider = FakeTikTokProvider()
    service = CredentialRefreshService(tiktok=provider, refresh_before_seconds=300)
    now = datetime.now(timezone.utc)

    with pytest.raises(RuntimeError, match="reconnect"):
        service.refresh_if_needed(
            "tiktok",
            {
                "access_token": "expiring",
                "token_expires_at": (now + timedelta(seconds=10)).isoformat(),
            },
        )


def test_other_platform_credentials_are_untouched():
    provider = FakeTikTokProvider()
    service = CredentialRefreshService(tiktok=provider)

    options, changed = service.refresh_if_needed(
        "instagram",
        {"access_token": "meta-token"},
    )

    assert changed is False
    assert options == {"access_token": "meta-token"}
    assert provider.calls == []
