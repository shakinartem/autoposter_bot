from __future__ import annotations

from autoposter_bot.config import Settings
from autoposter_bot.platforms.legacy import LegacyPublisherAdapter
from autoposter_bot.platforms.registry import PlatformRegistry
from autoposter_bot.publishers import InstagramPublisher, TelegramPublisher, TikTokPublisher, VkPublisher


def build_default_platform_registry(settings: Settings) -> PlatformRegistry:
    registry = PlatformRegistry()
    registry.register(
        LegacyPublisherAdapter(
            TelegramPublisher(settings.telegram_bot_token),
            content_types=("text", "image", "video", "carousel"),
            features={
                "formatting": True,
                "albums": True,
                "scheduled_publish": True,
            },
        )
    )
    registry.register(
        LegacyPublisherAdapter(
            VkPublisher(settings.vk_token, settings.vk_api_version),
            content_types=("text", "image", "video"),
            features={"scheduled_publish": True},
        )
    )
    registry.register(
        LegacyPublisherAdapter(
            InstagramPublisher(),
            content_types=(
                "instagram_feed_image",
                "instagram_video",
                "instagram_carousel",
                "instagram_story_image",
                "instagram_story_video",
            ),
            features={
                "carousel": True,
                "stories": True,
                "reels": True,
                "scheduled_publish": True,
            },
        )
    )
    registry.register(
        LegacyPublisherAdapter(
            TikTokPublisher(settings),
            content_types=("video",),
            features={"video": True, "scheduled_publish": True},
        )
    )
    return registry
