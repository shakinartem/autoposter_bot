from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import jwt
import requests
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from autoposter_bot.config import Settings


@dataclass(slots=True, frozen=True)
class TelegramLoginState:
    code_verifier: str
    nonce: str
    return_path: str


class TelegramLoginStateCodec:
    def __init__(self, fernet: MultiFernet, *, ttl_seconds: int = 600) -> None:
        self.fernet = fernet
        self.ttl_seconds = max(60, min(ttl_seconds, 1800))

    @classmethod
    def from_env(cls) -> "TelegramLoginStateCodec":
        raw = os.getenv("AUTOPOSTER_LOGIN_STATE_KEYS", "").strip()
        if not raw:
            raw = os.getenv("AUTOPOSTER_OAUTH_STATE_KEYS", "").strip()
        if not raw:
            raw = os.getenv("AUTOPOSTER_CREDENTIAL_KEYS", "").strip()
        keys = [item.strip() for item in raw.split(",") if item.strip()]
        if not keys:
            raise RuntimeError(
                "AUTOPOSTER_LOGIN_STATE_KEYS, AUTOPOSTER_OAUTH_STATE_KEYS or AUTOPOSTER_CREDENTIAL_KEYS is required for Telegram login state"
            )
        try:
            fernet = MultiFernet([Fernet(key.encode("ascii")) for key in keys])
        except Exception as exc:
            raise RuntimeError("Telegram login state key ring contains an invalid Fernet key") from exc
        ttl = int(os.getenv("AUTOPOSTER_LOGIN_STATE_TTL_SECONDS", "600"))
        return cls(fernet, ttl_seconds=ttl)

    def issue(self, *, return_path: str) -> tuple[str, TelegramLoginState]:
        now = datetime.now(timezone.utc)
        state = TelegramLoginState(
            code_verifier=_base64url(secrets.token_bytes(64)),
            nonce=secrets.token_urlsafe(32),
            return_path=_safe_return_path(return_path),
        )
        payload = {
            "code_verifier": state.code_verifier,
            "nonce": state.nonce,
            "return_path": state.return_path,
            "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=self.ttl_seconds)).isoformat(),
        }
        encrypted = self.fernet.encrypt(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")
        return encrypted, state

    def verify(self, token: str) -> TelegramLoginState:
        try:
            raw = self.fernet.decrypt(token.encode("ascii"), ttl=self.ttl_seconds)
        except InvalidToken as exc:
            raise ValueError("Telegram login state is invalid or expired") from exc
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Telegram login state payload is invalid")
        verifier = str(payload.get("code_verifier") or "")
        nonce = str(payload.get("nonce") or "")
        if len(verifier) < 43 or not nonce:
            raise ValueError("Telegram login state payload is incomplete")
        return TelegramLoginState(
            code_verifier=verifier,
            nonce=nonce,
            return_path=_safe_return_path(str(payload.get("return_path") or "/")),
        )


class TelegramOIDCProvider:
    AUTHORIZE_URL = "https://oauth.telegram.org/auth"
    TOKEN_URL = "https://oauth.telegram.org/token"
    JWKS_URL = "https://oauth.telegram.org/.well-known/jwks.json"
    ISSUER = "https://oauth.telegram.org"

    def __init__(self, settings: Settings) -> None:
        self.client_id = settings.telegram_oidc_client_id or ""
        self.client_secret = settings.telegram_oidc_client_secret or ""
        self.redirect_uri = settings.telegram_oidc_redirect_uri or ""
        scopes = [item for item in settings.telegram_oidc_scope.split() if item]
        if "openid" not in scopes:
            scopes.insert(0, "openid")
        self.scope = " ".join(dict.fromkeys(scopes))
        self.state_codec = TelegramLoginStateCodec.from_env() if self.is_configured() else None

    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.redirect_uri)

    def authorization_url(self, *, return_path: str = "/") -> str:
        if not self.is_configured() or self.state_codec is None:
            raise RuntimeError("Telegram OIDC login is not configured")
        encrypted_state, state = self.state_codec.issue(return_path=return_path)
        challenge = _base64url(hashlib.sha256(state.code_verifier.encode("ascii")).digest())
        return f"{self.AUTHORIZE_URL}?{urlencode({
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'response_type': 'code',
            'scope': self.scope,
            'state': encrypted_state,
            'nonce': state.nonce,
            'code_challenge': challenge,
            'code_challenge_method': 'S256',
        })}"

    def exchange_and_verify(self, *, code: str, state_token: str) -> tuple[dict[str, Any], str]:
        if not self.is_configured() or self.state_codec is None:
            raise RuntimeError("Telegram OIDC login is not configured")
        state = self.state_codec.verify(state_token)
        response = requests.post(
            self.TOKEN_URL,
            auth=(self.client_id, self.client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "client_id": self.client_id,
                "code_verifier": state.code_verifier,
            },
            timeout=30,
        )
        payload = response.json()
        if not response.ok or not payload.get("id_token"):
            raise RuntimeError(f"Telegram OIDC token exchange failed: {payload}")

        claims = self._verify_id_token(str(payload["id_token"]))
        if str(claims.get("nonce") or "") != state.nonce:
            raise RuntimeError("Telegram OIDC nonce mismatch")
        return claims, state.return_path

    def _verify_id_token(self, id_token: str) -> dict[str, Any]:
        allowed_algorithms = [
            item.strip()
            for item in os.getenv("TELEGRAM_OIDC_ALLOWED_ALGORITHMS", "RS256").split(",")
            if item.strip()
        ]
        jwk_client = jwt.PyJWKClient(self.JWKS_URL, cache_jwk_set=True, lifespan=300)
        signing_key = jwk_client.get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=allowed_algorithms,
            audience=self.client_id,
            issuer=self.ISSUER,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
        if not isinstance(claims, dict):
            raise RuntimeError("Telegram OIDC ID token payload is invalid")
        return claims


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _safe_return_path(value: str) -> str:
    value = value.strip()
    if not value.startswith("/") or value.startswith("//"):
        return "/"
    return value[:500]
