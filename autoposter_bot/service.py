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

    def sync_oauth_connections_for_user(self, owner_user_id: int) -> int:
        user = self.db.get_user(owner_user_id)
        telegram_user_id = int(user["telegram_user_id"]) if user else None
        connections = self.spgutils.list_connections(telegram_user_id=telegram_user_id)
        return self.db.sync_oauth_connections(connections, owner_user_id=owner_user_id)

    def list_oauth_connections_for_user(self, owner_user_id: int):
        return self.db.list_oauth_connections(owner_user_id=owner_user_id)
