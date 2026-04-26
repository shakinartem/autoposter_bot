from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from autoposter_bot.models import OAuthConnection


@dataclass(slots=True)
class SpgUtilsStatus:
    ok: bool
    detail: str
    payload: dict[str, Any] | list[Any] | None = None


class SpgUtilsClient:
    def __init__(
        self,
        base_url: str | None,
        api_token: str | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_token = api_token
        self.timeout_seconds = timeout_seconds
        self.http = requests.Session()

    def is_configured(self) -> bool:
        return bool(self.base_url)

    def start_oauth_link(
        self,
        provider: str,
        telegram_user_id: int,
        telegram_chat_id: int,
    ) -> dict[str, Any]:
        payload = self._request_json(
            "POST",
            "/api/link/start",
            json_body={
                "telegram_user_id": str(telegram_user_id),
                "telegram_chat_id": str(telegram_chat_id),
                "provider": provider,
            },
        )
        if not isinstance(payload, dict):
            raise ValueError(f"Unexpected link/start payload: {payload!r}")
        return payload

    def start_link(
        self,
        telegram_user_id: int,
        telegram_chat_id: int,
        provider: str,
    ) -> dict[str, Any]:
        return self.start_oauth_link(provider, telegram_user_id, telegram_chat_id)

    def get_link_result(self, link_token: str) -> dict[str, Any]:
        payload = self._request_json(
            "GET",
            "/api/link/result",
            params={"link_token": link_token},
        )
        if not isinstance(payload, dict):
            raise ValueError(f"Unexpected link/result payload: {payload!r}")
        return payload

    def list_connections(
        self,
        telegram_user_id: int | None = None,
        provider: str | None = None,
    ) -> list[OAuthConnection]:
        params: dict[str, Any] | None = None
        if telegram_user_id is not None or provider is not None:
            params = {}
            if telegram_user_id is not None:
                params["telegram_user_id"] = telegram_user_id
            if provider is not None:
                params["provider"] = provider
        payload = self._request_json(
            "GET",
            "/api/connections/list",
            params=params,
        )
        return self._parse_connections_payload(payload)

    def get_connection_token(self, connection_id: str | int) -> dict[str, Any]:
        payload = self._request_json(
            "GET",
            "/api/connections/token",
            params={"id": connection_id},
        )
        if not isinstance(payload, dict):
            raise ValueError(f"Unexpected connections/token payload: {payload!r}")
        return payload

    def get_meta_page(self, connection_id: str | int, page_id: str | int) -> dict[str, Any]:
        payload = self._request_json(
            "GET",
            "/api/meta/page",
            params={"id": page_id, "connection_id": connection_id},
        )
        if not isinstance(payload, dict):
            raise ValueError(f"Unexpected meta/page payload: {payload!r}")
        return payload

    def get_connections_status(self, telegram_user_id: int | None = None) -> SpgUtilsStatus:
        payload = self._request_json(
            "GET",
            "/api/connections/status",
            params={"telegram_user_id": telegram_user_id} if telegram_user_id is not None else None,
        )
        if isinstance(payload, dict):
            ok = bool(payload.get("ok", True))
            detail = str(payload.get("detail") or payload.get("message") or "ok")
        else:
            ok = True
            detail = "ok"
        return SpgUtilsStatus(ok=ok, detail=detail, payload=payload)

    def revoke_connection(self, connection_id: str | int) -> SpgUtilsStatus:
        payload = self._request_json(
            "POST",
            "/api/connections/revoke",
            json_body={
                "connection_id": str(connection_id),
            },
        )
        if isinstance(payload, dict):
            ok = bool(payload.get("ok", True))
            detail = str(payload.get("detail") or payload.get("message") or "revoked")
        else:
            ok = True
            detail = "revoked"
        return SpgUtilsStatus(ok=ok, detail=detail, payload=payload)

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        if not self.is_configured():
            raise RuntimeError("SPGUTILS_API_BASE_URL is not configured")
        headers = {"Accept": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        response = self.http.request(
            method,
            f"{self.base_url}{path}",
            params=params,
            json=json_body,
            headers=headers,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def _parse_connections_payload(self, payload: Any) -> list[OAuthConnection]:
        if isinstance(payload, dict):
            items = (
                payload.get("connections")
                or payload.get("items")
                or payload.get("results")
                or payload.get("data")
                or []
            )
        elif isinstance(payload, list):
            items = payload
        else:
            items = []
        return [self._parse_connection(item) for item in items if isinstance(item, dict)]

    def _parse_connection(self, payload: dict[str, Any]) -> OAuthConnection:
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        account = payload.get("account")
        if isinstance(account, dict):
            account_metadata = account.get("metadata")
            if isinstance(account_metadata, dict):
                metadata = {**metadata, **account_metadata}
            else:
                metadata = dict(metadata)
        remote_connection_id = self._coerce_str(
            payload.get("connection_id")
            or payload.get("connection_key")
            or payload.get("id")
            or payload.get("oauth_connection_id")
            or payload.get("account_external_id")
            or payload.get("account_id")
        )
        connection_key = remote_connection_id or ""
        if not connection_key:
            raise ValueError(f"Connection payload is missing a stable id: {payload}")
        provider = str(payload.get("provider") or payload.get("platform") or "unknown").lower()
        telegram_user_id = payload.get("telegram_user_id")
        if telegram_user_id is None:
            telegram_user_id = payload.get("user_id")
        account_external_id = payload.get("account_external_id") or payload.get("account_id")
        account_name = payload.get("account_name") or payload.get("name")
        destination = payload.get("destination") or payload.get("username") or payload.get("handle")
        access_token = payload.get("access_token")
        refresh_token = payload.get("refresh_token")
        token_type = payload.get("token_type")
        scope = payload.get("scope")
        scopes = payload.get("scopes")
        status = str(payload.get("status") or "active")
        expires_at = payload.get("expires_at") or payload.get("token_expires_at")
        synced_at = payload.get("synced_at") or payload.get("updated_at")
        provider_user_id = payload.get("provider_user_id") or payload.get("page_id") or payload.get("account_id")
        link_token = payload.get("link_token")
        revoked = payload.get("revoked")
        if isinstance(account, dict):
            extra_metadata = {k: v for k, v in account.items() if k not in {"id", "name", "username", "handle", "metadata"}}
            metadata = {**metadata, **extra_metadata}
        return OAuthConnection(
            id=None,
            remote_connection_id=connection_key,
            provider=provider,
            telegram_user_id=int(telegram_user_id) if telegram_user_id is not None and str(telegram_user_id).isdigit() else None,
            account_external_id=str(account_external_id) if account_external_id is not None else None,
            account_name=str(account_name) if account_name is not None else None,
            destination=str(destination) if destination is not None else None,
            access_token=str(access_token) if access_token is not None else None,
            refresh_token=str(refresh_token) if refresh_token is not None else None,
            token_type=str(token_type) if token_type is not None else None,
            scope=str(scope) if scope is not None else None,
            status=status,
            expires_at=str(expires_at) if expires_at is not None else None,
            metadata=metadata,
            synced_at=str(synced_at) if synced_at is not None else None,
            provider_user_id=self._coerce_str(provider_user_id),
            link_token=self._coerce_str(link_token),
            scopes=self._coerce_str(scopes) if scopes is not None else (self._coerce_scope_list(scope) if scope is not None else None),
            revoked=int(revoked) if revoked is not None and str(revoked).isdigit() else 0,
        )

    def _coerce_str(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _coerce_scope_list(self, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, (list, tuple, set)):
            items = [self._coerce_str(item) for item in value]
            return " ".join(item for item in items if item)
        text = str(value).strip()
        return text or None
