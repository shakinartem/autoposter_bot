from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from hashlib import sha256
from typing import Annotated

from fastapi import Header, HTTPException, status


@dataclass(frozen=True, slots=True)
class AuthContext:
    workspace_id: int
    credential_fingerprint: str
    mode: str = "api_key"


def _parse_api_keys() -> dict[str, int]:
    """Read API key -> workspace mappings from environment.

    Example:
      AUTOPOSTER_API_KEYS_JSON='{"dev-secret": 1, "team-secret": 2}'

    Raw keys are never logged or returned by the API.
    """
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
        "0",
        "false",
        "no",
        "off",
    }


def _dev_workspace_id() -> int:
    try:
        value = int(os.getenv("AUTOPOSTER_DEV_WORKSPACE_ID", "1"))
    except ValueError as exc:
        raise RuntimeError("AUTOPOSTER_DEV_WORKSPACE_ID must be an integer") from exc
    if value <= 0:
        raise RuntimeError("AUTOPOSTER_DEV_WORKSPACE_ID must be positive")
    return value


def get_auth_context(
    authorization: Annotated[str | None, Header()] = None,
) -> AuthContext:
    keys = _parse_api_keys()

    if not keys:
        if _require_auth():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="API authentication is required but AUTOPOSTER_API_KEYS_JSON is not configured",
            )
        return AuthContext(
            workspace_id=_dev_workspace_id(),
            credential_fingerprint="development",
            mode="development",
        )

    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not supplied:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer API key required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    matched_workspace: int | None = None
    for configured_key, workspace_id in keys.items():
        if secrets.compare_digest(supplied, configured_key):
            matched_workspace = workspace_id
            break

    if matched_workspace is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return AuthContext(
        workspace_id=matched_workspace,
        credential_fingerprint=sha256(supplied.encode("utf-8")).hexdigest()[:12],
    )
