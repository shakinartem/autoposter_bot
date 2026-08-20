from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


@dataclass(slots=True)
class OAuthIdentity:
    external_id: str
    display_name: str = ""
    username: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OAuthTokens:
    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    refresh_expires_at: datetime | None = None
    scope: str = ""
    token_type: str = "Bearer"
    metadata: dict[str, Any] = field(default_factory=dict)


class OAuthProvider(ABC):
    platform: str

    @abstractmethod
    def is_configured(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def authorization_url(self, state: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def exchange_code(self, code: str) -> OAuthTokens:
        raise NotImplementedError

    @abstractmethod
    def fetch_identity(self, tokens: OAuthTokens) -> OAuthIdentity:
        raise NotImplementedError


class OAuthStateCodec:
    """Short-lived encrypted OAuth state bound to one workspace and platform."""

    def __init__(self, fernet: MultiFernet, *, ttl_seconds: int = 600) -> None:
        self.fernet = fernet
        self.ttl_seconds = max(60, ttl_seconds)

    @classmethod
    def from_env(cls, *, required: bool = True) -> "OAuthStateCodec | None":
        raw = os.getenv("AUTOPOSTER_OAUTH_STATE_KEYS", "").strip()
        if not raw:
            raw = os.getenv("AUTOPOSTER_CREDENTIAL_KEYS", "").strip()
        keys = [item.strip() for item in raw.split(",") if item.strip()]
        if not keys:
            if required:
                raise RuntimeError(
                    "AUTOPOSTER_OAUTH_STATE_KEYS or AUTOPOSTER_CREDENTIAL_KEYS is required for OAuth state"
                )
            return None
        try:
            multi = MultiFernet([Fernet(key.encode("ascii")) for key in keys])
        except Exception as exc:
            raise RuntimeError("OAuth state key ring contains an invalid Fernet key") from exc
        ttl = int(os.getenv("AUTOPOSTER_OAUTH_STATE_TTL_SECONDS", "600"))
        return cls(multi, ttl_seconds=ttl)

    def issue(self, *, workspace_id: int, platform: str, return_path: str = "/accounts") -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "workspace_id": workspace_id,
            "platform": platform.lower(),
            "return_path": self._safe_return_path(return_path),
            "nonce": uuid4().hex,
            "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=self.ttl_seconds)).isoformat(),
        }
        return self.fernet.encrypt(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")

    def verify(self, token: str, *, expected_platform: str) -> dict[str, Any]:
        try:
            raw = self.fernet.decrypt(token.encode("ascii"), ttl=self.ttl_seconds)
        except InvalidToken as exc:
            raise ValueError("OAuth state is invalid or expired") from exc
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("OAuth state payload is invalid")
        if payload.get("platform") != expected_platform.lower():
            raise ValueError("OAuth state platform mismatch")
        workspace_id = payload.get("workspace_id")
        if not isinstance(workspace_id, int) or workspace_id <= 0:
            raise ValueError("OAuth state workspace is invalid")
        payload["return_path"] = self._safe_return_path(str(payload.get("return_path") or "/accounts"))
        return payload

    @staticmethod
    def _safe_return_path(value: str) -> str:
        value = value.strip()
        if not value.startswith("/") or value.startswith("//"):
            return "/accounts"
        return value[:500]
