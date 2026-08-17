from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from hashlib import sha256
from typing import Annotated, Callable

from fastapi import Header, HTTPException, status

from autoposter_bot.infrastructure.auth_store import SessionIdentity


@dataclass(frozen=True, slots=True)
class AuthContext:
    workspace_id: int
    credential_fingerprint: str
    mode: str = "api_key"
    user_id: int | None = None
    role: str = "service"
    session_id: str | None = None


_session_resolver: Callable[[str], SessionIdentity | None] | None = None


def configure_session_resolver(resolver: Callable[[str], SessionIdentity | None] | None) -> None:
    global _session_resolver
    _session_resolver = resolver


def _parse_api_keys() -> dict[str, int]:
    raw = os.getenv("AUTOPOSTER_API_KEYS_JSON", "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("AUTOPOSTER_API_KEYS_JSON must contain valid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("AUTOPOSTER_API_KEYS_JSON must be a JSON object")
    result: dict[str, int] = {}
    for key, workspace_id in payload.items():
        if not isinstance(key, str) or len(key) < 16:
            raise RuntimeError("Every Autoposter API key must be at least 16 characters")
        try:
            parsed_workspace_id = int(workspace_id)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Every Autoposter API key must map to an integer workspace id") from exc
        if parsed_workspace_id <= 0:
            raise RuntimeError("Workspace ids in AUTOPOSTER_API_KEYS_JSON must be positive")
        result[key] = parsed_workspace_id
    return result


def _require_auth() -> bool:
    return os.getenv("AUTOPOSTER_REQUIRE_API_AUTH", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def _dev_workspace_id() -> int:
    try:
        value = int(os.getenv("AUTOPOSTER_DEV_WORKSPACE_ID", "1"))
    except ValueError as exc:
        raise RuntimeError("AUTOPOSTER_DEV_WORKSPACE_ID must be an integer") from exc
    if value <= 0:
        raise RuntimeError("AUTOPOSTER_DEV_WORKSPACE_ID must be positive")
    return value


def get_auth_context(authorization: Annotated[str | None, Header()] = None) -> AuthContext:
    keys = _parse_api_keys()
    scheme, _, supplied = (authorization or "").partition(" ")

    if scheme.lower() == "bearer" and supplied:
        for configured_key, workspace_id in keys.items():
            if secrets.compare_digest(supplied, configured_key):
                return AuthContext(
                    workspace_id=workspace_id,
                    credential_fingerprint=sha256(supplied.encode()).hexdigest()[:12],
                    mode="api_key",
                    role="service",
                )
        if _session_resolver is not None:
            identity = _session_resolver(supplied)
            if identity is not None:
                return AuthContext(
                    workspace_id=identity.workspace_id,
                    credential_fingerprint=sha256(supplied.encode()).hexdigest()[:12],
                    mode="session",
                    user_id=identity.user_id,
                    role=identity.role,
                    session_id=identity.session_id,
                )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired bearer credential",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not keys and not _require_auth():
        return AuthContext(
            workspace_id=_dev_workspace_id(),
            credential_fingerprint="development",
            mode="development",
            role="service",
        )

    if not keys and _session_resolver is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is required but no authentication backend is configured",
        )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Bearer credential required",
        headers={"WWW-Authenticate": "Bearer"},
    )
