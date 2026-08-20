from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet

from autoposter_bot.integrations.oauth import OAuthStateCodec
from autoposter_bot.integrations.tiktok_oauth import TikTokOAuthProvider


class FakeSettings:
    tiktok_client_key = "client-key"
    tiktok_client_secret = "client-secret"
    tiktok_redirect_uri = "https://api.example.com/api/v1/oauth/tiktok/callback"
    tiktok_scope = "video.publish,video.upload"


def test_oauth_state_is_encrypted_workspace_bound_and_platform_bound(monkeypatch):
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("AUTOPOSTER_OAUTH_STATE_KEYS", key)
    codec = OAuthStateCodec.from_env(required=True)
    assert codec is not None

    state = codec.issue(workspace_id=42, platform="tiktok", return_path="/accounts")

    assert "workspace_id" not in state
    payload = codec.verify(state, expected_platform="tiktok")
    assert payload["workspace_id"] == 42
    assert payload["return_path"] == "/accounts"
    with pytest.raises(ValueError):
        codec.verify(state, expected_platform="instagram")


def test_oauth_state_rejects_external_return_url(monkeypatch):
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("AUTOPOSTER_OAUTH_STATE_KEYS", key)
    codec = OAuthStateCodec.from_env(required=True)
    assert codec is not None

    state = codec.issue(
        workspace_id=7,
        platform="tiktok",
        return_path="https://evil.example/phish",
    )

    payload = codec.verify(state, expected_platform="tiktok")
    assert payload["return_path"] == "/accounts"


def test_tiktok_authorization_url_contains_required_scope_and_state():
    provider = TikTokOAuthProvider(FakeSettings())
    url = provider.authorization_url("encrypted-state")
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "www.tiktok.com"
    assert query["client_key"] == ["client-key"]
    assert query["response_type"] == ["code"]
    assert query["state"] == ["encrypted-state"]
    assert "user.info.basic" in query["scope"][0]
    assert "video.publish" in query["scope"][0]
    assert query["redirect_uri"] == [FakeSettings.tiktok_redirect_uri]
