from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from autoposter_bot.integrations.instagram_oauth import InstagramOAuthProvider


def _provider(monkeypatch) -> InstagramOAuthProvider:
    monkeypatch.setenv("INSTAGRAM_APP_ID", "ig-app")
    monkeypatch.setenv("INSTAGRAM_APP_SECRET", "ig-secret")
    monkeypatch.setenv(
        "INSTAGRAM_REDIRECT_URI",
        "https://api.example.com/api/v1/oauth/instagram/callback",
    )
    monkeypatch.setenv(
        "INSTAGRAM_SCOPE",
        "instagram_business_basic,instagram_business_content_publish",
    )
    return InstagramOAuthProvider()


def test_instagram_authorization_url_is_state_bound(monkeypatch):
    provider = _provider(monkeypatch)
    url = provider.authorization_url("encrypted-state")
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "www.instagram.com"
    assert query["client_id"] == ["ig-app"]
    assert query["response_type"] == ["code"]
    assert query["state"] == ["encrypted-state"]
    assert query["redirect_uri"] == [
        "https://api.example.com/api/v1/oauth/instagram/callback"
    ]
    assert "instagram_business_basic" in query["scope"][0]
    assert "instagram_business_content_publish" in query["scope"][0]


def test_instagram_oauth_requires_client_secret(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_APP_ID", "ig-app")
    monkeypatch.delenv("INSTAGRAM_APP_SECRET", raising=False)
    monkeypatch.setenv(
        "INSTAGRAM_REDIRECT_URI",
        "https://api.example.com/api/v1/oauth/instagram/callback",
    )
    provider = InstagramOAuthProvider()
    assert provider.is_configured() is False
