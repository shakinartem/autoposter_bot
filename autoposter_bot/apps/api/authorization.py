from __future__ import annotations

from fastapi import HTTPException, status

from autoposter_bot.infrastructure.auth_store import ROLE_LEVEL, normalize_role


def role_level(role: str) -> int:
    value = role if role == "service" else normalize_role(role)
    return ROLE_LEVEL.get(value, 0)


def require_minimum_role(role: str, minimum: str) -> None:
    required = ROLE_LEVEL.get(minimum)
    if required is None:
        raise RuntimeError(f"Unknown workspace role: {minimum}")
    if role_level(role) < required:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{minimum} workspace role required",
        )
