from __future__ import annotations

from autoposter_bot.config import Settings
from autoposter_bot.db import Database
from autoposter_bot.models import PostJob, Target
from autoposter_bot.publishers import (
    InstagramPublisher,
    PublishResult,
    TelegramPublisher,
    TikTokPublisher,
    VkPublisher,
)
from autoposter_bot.spgutils_client import SpgUtilsClient


class AutoposterService:
    def __init__(self, settings: Settings) -> None:
        self.db = Database(settings.database_path)
        self.spgutils = SpgUtilsClient(
            settings.spgutils_api_base_url,
            settings.spgutils_api_token,
            settings.spgutils_timeout_seconds,
        )
        self.publishers = {
            "telegram": TelegramPublisher(settings.telegram_bot_token),
            "vk": VkPublisher(settings.vk_token, settings.vk_api_version),
            "instagram": InstagramPublisher(),
            "tiktok": TikTokPublisher(settings),
        }

    def publish_job(self, job: PostJob, dry_run: bool = False) -> list[PublishResult]:
        results: list[PublishResult] = []
        owner_user_id = job.metadata.get("owner_user_id")
        if owner_user_id is not None:
            try:
                owner_user_id = int(owner_user_id)
            except (TypeError, ValueError):
                owner_user_id = None
        for target in job.targets:
            effective_target = target
            if target.account_id is not None:
                resolved_options = self.db.resolve_account_options(int(target.account_id), owner_user_id=owner_user_id)
                merged_options = dict(resolved_options)
                merged_options.update(target.options)
                merged_options = self._hydrate_publish_options(
                    account_id=int(target.account_id),
                    platform=target.platform,
                    options=merged_options,
                    owner_user_id=owner_user_id,
                )
                effective_target = Target(
                    platform=target.platform,
                    destination=target.destination,
                    account_id=target.account_id,
                    account_name=target.account_name,
                    options=merged_options,
                )
            publisher = self.publishers.get(effective_target.platform.lower())
            if not publisher:
                results.append(
                    PublishResult(
                        effective_target.platform,
                        effective_target.destination,
                        False,
                        f"Unsupported platform: {effective_target.platform}",
                    )
                )
                continue
            results.append(publisher.publish(job, effective_target, dry_run=dry_run))
        return results

    def start_oauth_link(self, telegram_user_id: int, telegram_chat_id: int, provider: str) -> dict:
        return self.spgutils.start_link(telegram_user_id, telegram_chat_id, provider)

    def get_oauth_link_result(self, link_token: str) -> dict:
        return self.spgutils.get_link_result(link_token)

    def sync_oauth_connections_for_user(self, owner_user_id: int) -> int:
        user = self.db.get_user(owner_user_id)
        telegram_user_id = int(user["telegram_user_id"]) if user else None
        if telegram_user_id is None:
            return 0
        connections = self.spgutils.list_connections(telegram_user_id=telegram_user_id)
        return self.db.sync_oauth_connections(connections, owner_user_id=owner_user_id)

    def list_oauth_connections_for_user(self, owner_user_id: int):
        return self.db.list_oauth_connections(owner_user_id=owner_user_id)

    def _hydrate_publish_options(
        self,
        *,
        account_id: int,
        platform: str,
        options: dict,
        owner_user_id: int | None,
    ) -> dict:
        connection_id = options.get("oauth_connection_id") or options.get("oauth_connection_key")
        if not connection_id:
            return options
        hydrated = dict(options)
        token_payload = self.spgutils.get_connection_token(str(connection_id))
        hydrated.update(self._extract_token_options(token_payload))
        provider = str(hydrated.get("oauth_provider") or hydrated.get("provider") or platform).lower()
        local_platform = self._local_platform_for_provider(provider or platform)
        if local_platform == "instagram":
            page_id = (
                hydrated.get("meta_page_id")
                or hydrated.get("page_id")
                or hydrated.get("oauth_provider_user_id")
                or hydrated.get("oauth_account_external_id")
            )
            if page_id is not None:
                page_payload = self.spgutils.get_meta_page(str(page_id), str(connection_id))
                hydrated.update(self._extract_meta_page_options(page_payload))
        return hydrated

    def _extract_token_options(self, payload: dict) -> dict:
        sources: list[dict] = [payload]
        for key in ("data", "connection", "token", "result"):
            value = payload.get(key)
            if isinstance(value, dict):
                sources.append(value)
        options: dict[str, object] = {}
        for source in sources:
            for key in ("access_token", "refresh_token", "token_type", "scope", "scopes", "expires_at", "expires_in"):
                value = source.get(key)
                if value is not None and key not in options:
                    options[key] = value
        if "scope" in options and "scopes" not in options:
            options["scopes"] = options["scope"]
        return options

    def _extract_meta_page_options(self, payload: dict) -> dict:
        sources: list[dict] = [payload]
        for key in ("data", "page", "connection", "result"):
            value = payload.get(key)
            if isinstance(value, dict):
                sources.append(value)
        options: dict[str, object] = {}
        for source in sources:
            for key in (
                "page_id",
                "meta_page_id",
                "ig_user_id",
                "instagram_user_id",
                "page_access_token",
                "access_token",
                "username",
                "name",
                "destination",
            ):
                value = source.get(key)
                if value is not None and key not in options:
                    options[key] = value
        if "page_access_token" in options and "access_token" not in options:
            options["access_token"] = options["page_access_token"]
        if "meta_page_id" not in options and options.get("page_id") is not None:
            options["meta_page_id"] = options["page_id"]
        if "ig_user_id" not in options and options.get("instagram_user_id") is not None:
            options["ig_user_id"] = options["instagram_user_id"]
        if "destination" not in options:
            destination = options.get("username") or options.get("name")
            if destination is not None:
                options["destination"] = destination
        return options

    def _local_platform_for_provider(self, provider: str) -> str:
        provider = provider.lower()
        if provider == "meta":
            return "instagram"
        return provider
