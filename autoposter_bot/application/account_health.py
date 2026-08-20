from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import requests

from autoposter_bot.infrastructure.account_health_store import AccountHealthStore
from autoposter_bot.integrations.credential_refresh import CredentialRefreshService
from autoposter_bot.integrations.instagram_oauth import InstagramOAuthProvider
from autoposter_bot.integrations.tiktok_oauth import TikTokOAuthProvider


@dataclass(slots=True)
class AccountProbeStats:
    workspaces: int = 0
    accounts: int = 0
    healthy: int = 0
    degraded: int = 0
    critical: int = 0


class AccountHealthApplication:
    """Read-only connection probes. No probe may publish, edit or delete remote data."""

    def __init__(
        self,
        *,
        account_health: AccountHealthStore,
        store_for_workspace: Callable[[int], Any],
        credential_refresh: CredentialRefreshService,
        tiktok: TikTokOAuthProvider,
        instagram: InstagramOAuthProvider,
        telegram_bot_token: str | None,
        http_get: Callable[..., Any] = requests.get,
        timeout_seconds: int = 20,
    ) -> None:
        self.account_health = account_health
        self.store_for_workspace = store_for_workspace
        self.credential_refresh = credential_refresh
        self.tiktok = tiktok
        self.instagram = instagram
        self.telegram_bot_token = (telegram_bot_token or "").strip()
        self.http_get = http_get
        self.timeout_seconds = max(3, timeout_seconds)
        self._telegram_probe_cache: dict[str, Any] | None = None

    def run_once(self, *, now: datetime | None = None) -> AccountProbeStats:
        now = self._aware(now or datetime.now(timezone.utc))
        self._telegram_probe_cache = None
        stats = AccountProbeStats()
        for workspace in self.account_health.list_workspaces():
            stats.workspaces += 1
            results = self.probe_workspace(int(workspace["id"]), now=now)
            for result in results:
                stats.accounts += 1
                setattr(stats, result["status"], getattr(stats, result["status"]) + 1)
        return stats

    def probe_workspace(self, workspace_id: int, *, now: datetime | None = None) -> list[dict[str, Any]]:
        scoped = self.store_for_workspace(workspace_id)
        return [self.probe_account(workspace_id, int(account["id"]), now=now) for account in scoped.list_accounts()]

    def probe_account(
        self,
        workspace_id: int,
        account_id: int,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = self._aware(now or datetime.now(timezone.utc))
        scoped = self.store_for_workspace(workspace_id)
        account = scoped.get_account(account_id)
        if account is None:
            raise KeyError(account_id)
        platform = str(account["platform"]).strip().lower()
        options = dict(account.get("options") or {})

        if platform in {"tiktok", "instagram"}:
            try:
                options, changed = self.credential_refresh.refresh_if_needed(platform, options)
                if changed:
                    refreshed = scoped.update_account(account_id, options=options)
                    if refreshed is not None:
                        account = refreshed
                        options = dict(refreshed.get("options") or options)
            except Exception as exc:
                return self._persist(
                    workspace_id=workspace_id,
                    account=account,
                    status="critical",
                    code="credential_refresh_failed",
                    message=self._safe_error(exc),
                    probe_method="credential_refresh",
                    reconnect_required=True,
                    token_expires_at=options.get("token_expires_at"),
                    checked_at=now,
                )

        if platform == "tiktok":
            result = self._probe_tiktok(options)
        elif platform == "instagram":
            result = self._probe_instagram(options)
        elif platform == "telegram":
            result = self._probe_telegram(account)
        elif platform == "vk":
            result = self._probe_vk(options)
        else:
            result = {
                "status": "degraded",
                "code": "probe_not_supported",
                "message": f"Read-only health probe is not implemented for {platform}",
                "probe_method": "local_only",
                "identity": {},
                "reconnect_required": False,
            }

        return self._persist(
            workspace_id=workspace_id,
            account=account,
            token_expires_at=options.get("token_expires_at"),
            checked_at=now,
            **result,
        )

    def _probe_tiktok(self, options: dict[str, Any]) -> dict[str, Any]:
        token = str(options.get("access_token") or "").strip()
        if not token:
            return self._missing_secret("TikTok access token is missing")
        try:
            response = self.http_get(
                self.tiktok.USER_INFO_URL,
                headers={"Authorization": f"Bearer {token}"},
                params={"fields": "open_id,union_id,avatar_url,display_name"},
                timeout=self.timeout_seconds,
            )
            payload = self._payload(response)
        except Exception as exc:
            return self._transient("TikTok identity probe failed", exc)
        error = payload.get("error") or {}
        user = (payload.get("data") or {}).get("user") or {}
        error_code = str(error.get("code") or "").strip().lower()
        if not getattr(response, "ok", False) or error_code not in {"", "ok"} or not user.get("open_id"):
            return self._remote_failure("TikTok", response, payload, error_code)
        return {
            "status": "healthy",
            "code": "remote_identity_verified",
            "message": "TikTok access is valid and user identity is readable",
            "probe_method": "tiktok.user.info",
            "identity": {
                "external_id": str(user["open_id"]),
                "display_name": str(user.get("display_name") or "TikTok account"),
                "union_id": user.get("union_id"),
                "avatar_url": user.get("avatar_url"),
            },
            "reconnect_required": False,
        }

    def _probe_instagram(self, options: dict[str, Any]) -> dict[str, Any]:
        token = str(options.get("access_token") or "").strip()
        if not token:
            return self._missing_secret("Instagram access token is missing")
        if str(options.get("api_flow") or "instagram_login") != "instagram_login":
            return {
                "status": "degraded",
                "code": "probe_not_supported",
                "message": "This legacy Instagram API flow is not covered by the read-only Instagram Login probe",
                "probe_method": "local_only",
                "identity": {},
                "reconnect_required": False,
            }
        try:
            response = self.http_get(
                self.instagram.USER_INFO_URL,
                params={
                    "fields": "user_id,username,name,account_type,profile_picture_url",
                    "access_token": token,
                },
                timeout=self.timeout_seconds,
            )
            payload = self._payload(response)
        except Exception as exc:
            return self._transient("Instagram identity probe failed", exc)
        external_id = payload.get("user_id") or payload.get("id") or options.get("ig_user_id")
        if not getattr(response, "ok", False) or not external_id:
            return self._remote_failure("Instagram", response, payload, str((payload.get("error") or {}).get("code") or ""))
        return {
            "status": "healthy",
            "code": "remote_identity_verified",
            "message": "Instagram access is valid and account identity is readable",
            "probe_method": "instagram.me",
            "identity": {
                "external_id": str(external_id),
                "username": str(payload.get("username") or ""),
                "display_name": str(payload.get("name") or payload.get("username") or "Instagram account"),
                "account_type": payload.get("account_type"),
            },
            "reconnect_required": False,
        }

    def _probe_telegram(self, account: dict[str, Any]) -> dict[str, Any]:
        if not str(account.get("destination") or "").strip():
            return {
                "status": "critical",
                "code": "destination_missing",
                "message": "Telegram destination is missing",
                "probe_method": "local_validation",
                "identity": {},
                "reconnect_required": False,
            }
        if not self.telegram_bot_token:
            return {
                "status": "critical",
                "code": "telegram_bot_unconfigured",
                "message": "System Telegram bot token is not configured",
                "probe_method": "local_validation",
                "identity": {},
                "reconnect_required": False,
            }
        if self._telegram_probe_cache is not None:
            return dict(self._telegram_probe_cache)
        try:
            response = self.http_get(
                f"https://api.telegram.org/bot{self.telegram_bot_token}/getMe",
                timeout=self.timeout_seconds,
            )
            payload = self._payload(response)
        except Exception as exc:
            result = self._transient("Telegram getMe probe failed", exc)
            self._telegram_probe_cache = result
            return dict(result)
        result_data = payload.get("result") or {}
        if not getattr(response, "ok", False) or payload.get("ok") is not True or not result_data.get("id"):
            result = self._remote_failure("Telegram", response, payload, str(payload.get("error_code") or ""))
            self._telegram_probe_cache = result
            return dict(result)
        result = {
            "status": "healthy",
            "code": "remote_identity_verified",
            "message": "Telegram bot token is valid; target chat permissions are not modified by this probe",
            "probe_method": "telegram.getMe",
            "identity": {
                "external_id": str(result_data["id"]),
                "username": str(result_data.get("username") or ""),
                "display_name": str(result_data.get("first_name") or "Telegram bot"),
            },
            "reconnect_required": False,
        }
        self._telegram_probe_cache = result
        return dict(result)

    @staticmethod
    def _probe_vk(options: dict[str, Any]) -> dict[str, Any]:
        if not str(options.get("access_token") or "").strip():
            return {
                "status": "critical",
                "code": "credential_missing",
                "message": "VK access token is missing",
                "probe_method": "local_validation",
                "identity": {},
                "reconnect_required": True,
            }
        return {
            "status": "degraded",
            "code": "probe_not_verified",
            "message": "VK token is configured, but no verified read-only remote identity probe is enabled yet",
            "probe_method": "local_validation",
            "identity": {},
            "reconnect_required": False,
        }

    @staticmethod
    def _payload(response: Any) -> dict[str, Any]:
        try:
            payload = response.json()
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _remote_failure(provider: str, response: Any, payload: dict[str, Any], provider_code: str) -> dict[str, Any]:
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code == 429 or status_code >= 500:
            return {
                "status": "degraded",
                "code": "provider_temporarily_unavailable",
                "message": f"{provider} read-only probe returned HTTP {status_code or 'error'}",
                "probe_method": "remote_identity",
                "identity": {},
                "reconnect_required": False,
            }
        normalized = provider_code.lower()
        authish = status_code in {400, 401, 403} or any(
            token in normalized for token in ("token", "scope", "permission", "auth", "unauthorized")
        )
        return {
            "status": "critical" if authish else "degraded",
            "code": "credential_rejected" if authish else "remote_probe_failed",
            "message": f"{provider} read-only probe rejected the stored connection",
            "probe_method": "remote_identity",
            "identity": {},
            "reconnect_required": authish,
        }

    @staticmethod
    def _missing_secret(message: str) -> dict[str, Any]:
        return {
            "status": "critical",
            "code": "credential_missing",
            "message": message,
            "probe_method": "local_validation",
            "identity": {},
            "reconnect_required": True,
        }

    @classmethod
    def _transient(cls, prefix: str, exc: Exception) -> dict[str, Any]:
        return {
            "status": "degraded",
            "code": "probe_transport_error",
            "message": f"{prefix}: {cls._safe_error(exc)}",
            "probe_method": "remote_identity",
            "identity": {},
            "reconnect_required": False,
        }

    def _persist(
        self,
        *,
        workspace_id: int,
        account: dict[str, Any],
        status: str,
        code: str,
        message: str,
        probe_method: str,
        identity: dict[str, Any],
        reconnect_required: bool,
        token_expires_at: datetime | str | None,
        checked_at: datetime,
    ) -> dict[str, Any]:
        return self.account_health.upsert(
            workspace_id=workspace_id,
            account_id=int(account["id"]),
            platform=str(account["platform"]),
            status=status,
            code=code,
            message=message,
            probe_method=probe_method,
            identity=identity,
            reconnect_required=reconnect_required,
            token_expires_at=token_expires_at,
            checked_at=checked_at,
        )

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        text = str(exc).strip() or type(exc).__name__
        return text[:500]

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
