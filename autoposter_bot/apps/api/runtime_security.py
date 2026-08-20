"""Fail-closed runtime validation for internet-facing Autoposter deployments."""
from __future__ import annotations

import json
import os
from urllib.parse import urlparse


def _truthy(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def is_production() -> bool:
    return os.getenv("AUTOPOSTER_ENV", "development").strip().lower() in {"prod", "production"}


def _require(errors: list[str], name: str, *, min_length: int = 1) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        errors.append(f"{name} is required")
    elif len(value) < min_length:
        errors.append(f"{name} must be at least {min_length} characters")
    return value


def validate_runtime_security(*, require_content_factory: bool = False) -> None:
    if not is_production():
        return
    errors: list[str] = []

    database_url = _require(errors, "AUTOPOSTER_DATABASE_URL")
    if database_url and not database_url.startswith(("postgresql://", "postgres://")):
        errors.append("AUTOPOSTER_DATABASE_URL must use PostgreSQL in production")
    if not _truthy("AUTOPOSTER_REQUIRE_API_AUTH", "true"):
        errors.append("AUTOPOSTER_REQUIRE_API_AUTH must be enabled in production")
    if os.getenv("AUTOPOSTER_WEB_AUTH_MODE", "").strip().lower() != "session":
        errors.append("AUTOPOSTER_WEB_AUTH_MODE must be session in production")

    _require(errors, "AUTOPOSTER_CREDENTIAL_KEYS", min_length=40)
    web_url = _require(errors, "AUTOPOSTER_WEB_URL")
    if web_url and urlparse(web_url).scheme != "https":
        errors.append("AUTOPOSTER_WEB_URL must use https")

    origins = [part.strip() for part in os.getenv("AUTOPOSTER_CORS_ORIGINS", "").split(",") if part.strip()]
    if not origins or "*" in origins:
        errors.append("AUTOPOSTER_CORS_ORIGINS must explicitly list production origins")
    hosts = [part.strip() for part in os.getenv("AUTOPOSTER_ALLOWED_HOSTS", "").split(",") if part.strip()]
    if not hosts or "*" in hosts:
        errors.append("AUTOPOSTER_ALLOWED_HOSTS must explicitly list production hosts")

    # Primary public login for the current product is Telegram OIDC.
    _require(errors, "TELEGRAM_OIDC_CLIENT_ID")
    _require(errors, "TELEGRAM_OIDC_CLIENT_SECRET", min_length=16)
    redirect = _require(errors, "TELEGRAM_OIDC_REDIRECT_URI")
    if redirect and urlparse(redirect).scheme != "https":
        errors.append("TELEGRAM_OIDC_REDIRECT_URI must use https")

    if require_content_factory:
        _require(errors, "CONTENT_FACTORY_INGEST_TOKEN", min_length=24)
        _require(errors, "CONTENT_FACTORY_PERFORMANCE_TOKEN", min_length=24)
        endpoint = _require(errors, "CONTENT_FACTORY_PERFORMANCE_URL")
        if endpoint and urlparse(endpoint).scheme not in {"http", "https"}:
            errors.append("CONTENT_FACTORY_PERFORMANCE_URL must be http(s)")
        if not _truthy("CONTENT_FACTORY_MEDIA_REQUIRE_ALLOWLIST", "false"):
            errors.append("CONTENT_FACTORY_MEDIA_REQUIRE_ALLOWLIST must be enabled in production")
        if _truthy("CONTENT_FACTORY_MEDIA_ALLOW_PRIVATE_HOSTS", "false"):
            errors.append("CONTENT_FACTORY_MEDIA_ALLOW_PRIVATE_HOSTS must be false in production")
        media_hosts = [part.strip().lower() for part in os.getenv("CONTENT_FACTORY_MEDIA_ALLOWED_HOSTS", "").split(",") if part.strip()]
        if not media_hosts:
            errors.append("CONTENT_FACTORY_MEDIA_ALLOWED_HOSTS must contain the Factory asset host")
        for host in media_hosts:
            if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
                errors.append(f"Unsafe production media allowlist host: {host}")

    raw_keys = os.getenv("AUTOPOSTER_API_KEYS_JSON", "").strip()
    if raw_keys:
        try:
            parsed = json.loads(raw_keys)
            if not isinstance(parsed, dict):
                raise ValueError
            for key in parsed:
                if not isinstance(key, str) or len(key) < 24:
                    errors.append("Every production service API key must be at least 24 characters")
                    break
        except (json.JSONDecodeError, ValueError):
            errors.append("AUTOPOSTER_API_KEYS_JSON must contain a JSON object")

    if errors:
        raise RuntimeError("Unsafe Autoposter production configuration: " + "; ".join(errors))
