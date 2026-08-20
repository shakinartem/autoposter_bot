#!/usr/bin/env python3
"""Validate Autoposter production settings before starting containers."""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse


def load_env(path: Path) -> dict[str, str]:
    values = dict(os.environ)
    if path.exists():
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values.setdefault(key.strip(), value.strip().strip('"\''))
    return values


def truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default=".env")
    args = parser.parse_args()
    values = load_env(Path(args.env))
    errors: list[str] = []
    warnings: list[str] = []

    required = {
        "AUTOPOSTER_DOMAIN": 1,
        "POSTGRES_PASSWORD": 24,
        "AUTOPOSTER_CREDENTIAL_KEYS": 40,
        "TELEGRAM_OIDC_CLIENT_ID": 1,
        "TELEGRAM_OIDC_CLIENT_SECRET": 16,
        "TELEGRAM_OIDC_REDIRECT_URI": 1,
        "CONTENT_FACTORY_INGEST_TOKEN": 24,
        "CONTENT_FACTORY_PERFORMANCE_URL": 1,
        "CONTENT_FACTORY_PERFORMANCE_TOKEN": 24,
        "CONTENT_FACTORY_MEDIA_ALLOWED_HOSTS": 1,
    }
    for name, min_length in required.items():
        value = values.get(name, "").strip()
        if not value:
            errors.append(f"{name} is required")
        elif len(value) < min_length:
            errors.append(f"{name} is too short (<{min_length})")

    if values.get("AUTOPOSTER_WEB_AUTH_MODE", "session").strip().lower() != "session":
        errors.append("AUTOPOSTER_WEB_AUTH_MODE must be session")
    if not truthy(values.get("AUTOPOSTER_REQUIRE_API_AUTH", "1")):
        errors.append("AUTOPOSTER_REQUIRE_API_AUTH must be enabled")
    if not truthy(values.get("CONTENT_FACTORY_MEDIA_REQUIRE_ALLOWLIST", "true")):
        errors.append("CONTENT_FACTORY_MEDIA_REQUIRE_ALLOWLIST must be enabled")
    if truthy(values.get("CONTENT_FACTORY_MEDIA_ALLOW_PRIVATE_HOSTS", "false")):
        errors.append("CONTENT_FACTORY_MEDIA_ALLOW_PRIVATE_HOSTS must be false")

    domain = values.get("AUTOPOSTER_DOMAIN", "").strip()
    web_url = values.get("AUTOPOSTER_WEB_URL", f"https://{domain}" if domain else "")
    if web_url and urlparse(web_url).scheme != "https":
        errors.append("AUTOPOSTER_WEB_URL must use https")
    redirect = values.get("TELEGRAM_OIDC_REDIRECT_URI", "")
    if redirect and urlparse(redirect).scheme != "https":
        errors.append("TELEGRAM_OIDC_REDIRECT_URI must use https")

    allowed_hosts = [x.strip() for x in values.get("AUTOPOSTER_ALLOWED_HOSTS", "").split(",") if x.strip()]
    if not allowed_hosts or "*" in allowed_hosts:
        errors.append("AUTOPOSTER_ALLOWED_HOSTS must explicitly list production hosts")
    cors = [x.strip() for x in values.get("AUTOPOSTER_CORS_ORIGINS", "").split(",") if x.strip()]
    if not cors or "*" in cors:
        errors.append("AUTOPOSTER_CORS_ORIGINS must explicitly list production origins")

    media_hosts = [x.strip().lower() for x in values.get("CONTENT_FACTORY_MEDIA_ALLOWED_HOSTS", "").split(",") if x.strip()]
    for host in media_hosts:
        if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
            errors.append(f"Unsafe Factory media allowlist host: {host}")

    # Fernet keys decode to exactly 32 bytes. Validate every rotation key without exposing it.
    keys = [x.strip() for x in values.get("AUTOPOSTER_CREDENTIAL_KEYS", "").split(",") if x.strip()]
    for index, key in enumerate(keys):
        try:
            decoded = base64.urlsafe_b64decode(key.encode("ascii"))
            if len(decoded) != 32:
                raise ValueError
        except Exception:
            errors.append(f"AUTOPOSTER_CREDENTIAL_KEYS key #{index + 1} is not a valid Fernet key")

    for state_name in ("AUTOPOSTER_LOGIN_STATE_KEYS", "AUTOPOSTER_OAUTH_STATE_KEYS"):
        if not values.get(state_name, "").strip():
            warnings.append(f"{state_name} is empty; credential encryption keys will also sign state tokens")

    api_keys = values.get("AUTOPOSTER_API_KEYS_JSON", "").strip()
    if api_keys:
        try:
            payload = json.loads(api_keys)
            if not isinstance(payload, dict):
                raise ValueError
            if any(not isinstance(key, str) or len(key) < 24 for key in payload):
                errors.append("Every production service API key must be at least 24 characters")
        except (json.JSONDecodeError, ValueError):
            errors.append("AUTOPOSTER_API_KEYS_JSON must be a JSON object")

    if not values.get("AUTOPOSTER_S3_BUCKET"):
        warnings.append("AUTOPOSTER_S3_BUCKET is empty; media will use the local app_data volume and must be backed up")

    if errors:
        print("AUTOPOSTER PREFLIGHT FAILED", file=sys.stderr)
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        for warning in warnings:
            print(f"WARN: {warning}", file=sys.stderr)
        return 1
    print("AUTOPOSTER PREFLIGHT OK")
    for warning in warnings:
        print(f"WARN: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
