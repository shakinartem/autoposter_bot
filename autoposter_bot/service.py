from __future__ import annotations

from autoposter_bot.config import Settings
from autoposter_bot.db import Database
from autoposter_bot.models import PostJob
from autoposter_bot.publishers import (
    InstagramPublisher,
    PublishResult,
    TelegramPublisher,
    TikTokPublisher,
    VkPublisher,
)


class AutoposterService:
    def __init__(self, settings: Settings) -> None:
        self.db = Database(settings.database_path)
        self.publishers = {
            "telegram": TelegramPublisher(settings.telegram_bot_token),
            "vk": VkPublisher(settings.vk_token, settings.vk_api_version),
            "instagram": InstagramPublisher(),
            "tiktok": TikTokPublisher(settings),
        }

    def publish_job(self, job: PostJob, dry_run: bool = False) -> list[PublishResult]:
        results: list[PublishResult] = []
        for target in job.targets:
            publisher = self.publishers.get(target.platform.lower())
            if not publisher:
                results.append(
                    PublishResult(
                        target.platform,
                        target.destination,
                        False,
                        f"Unsupported platform: {target.platform}",
                    )
                )
                continue
            results.append(publisher.publish(job, target, dry_run=dry_run))
        return results
