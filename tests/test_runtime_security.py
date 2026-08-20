from __future__ import annotations

import base64

import pytest

from autoposter_bot.apps.api.runtime_security import validate_runtime_security


def _safe_env(monkeypatch):
    values = {
        "AUTOPOSTER_ENV": "production",
        "AUTOPOSTER_DATABASE_URL": "postgresql://u:p@postgres/autoposter",
        "AUTOPOSTER_REQUIRE_API_AUTH": "1",
        "AUTOPOSTER_WEB_AUTH_MODE": "session",
        "AUTOPOSTER_CREDENTIAL_KEYS": base64.urlsafe_b64encode(b"x" * 32).decode(),
        "AUTOPOSTER_WEB_URL": "https://autoposter.example.com",
        "AUTOPOSTER_CORS_ORIGINS": "https://autoposter.example.com",
        "AUTOPOSTER_ALLOWED_HOSTS": "autoposter.example.com,autoposter-api",
        "TELEGRAM_OIDC_CLIENT_ID": "client",
        "TELEGRAM_OIDC_CLIENT_SECRET": "client-secret-123456789",
        "TELEGRAM_OIDC_REDIRECT_URI": "https://autoposter.example.com/auth/telegram/callback",
        "CONTENT_FACTORY_INGEST_TOKEN": "i" * 32,
        "CONTENT_FACTORY_PERFORMANCE_TOKEN": "p" * 32,
        "CONTENT_FACTORY_PERFORMANCE_URL": "http://content-factory-api:8000/performance/ingest",
        "CONTENT_FACTORY_MEDIA_REQUIRE_ALLOWLIST": "true",
        "CONTENT_FACTORY_MEDIA_ALLOW_PRIVATE_HOSTS": "false",
        "CONTENT_FACTORY_MEDIA_ALLOWED_HOSTS": "assets.factory.example.com",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_production_runtime_validation_fails_closed(monkeypatch):
    monkeypatch.setenv("AUTOPOSTER_ENV", "production")
    monkeypatch.delenv("AUTOPOSTER_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        validate_runtime_security(require_content_factory=True)


def test_production_runtime_validation_accepts_explicit_secure_config(monkeypatch):
    _safe_env(monkeypatch)
    validate_runtime_security(require_content_factory=True)


def test_production_bridge_rejects_private_media_escape_hatch(monkeypatch):
    _safe_env(monkeypatch)
    monkeypatch.setenv("CONTENT_FACTORY_MEDIA_ALLOW_PRIVATE_HOSTS", "true")
    with pytest.raises(RuntimeError):
        validate_runtime_security(require_content_factory=True)
