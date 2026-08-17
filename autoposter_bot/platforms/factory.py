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
            fields={
                "parse_mode": {
                    "type": "select",
                    "label": "Форматирование",
                    "options": ["HTML", "MarkdownV2", "plain"],
                    "default": "HTML",
                },
                "disable_notification": {
                    "type": "boolean",
                    "label": "Без уведомления",
                    "default": False,
                },
            },
            features={"formatting": True, "albums": True, "scheduled_publish": True},
            limits={"caption_chars": 1024, "text_chars": 4096},
        )
    )
    registry.register(
        LegacyPublisherAdapter(
            VkPublisher(settings.vk_token, settings.vk_api_version),
            content_types=("text", "image", "video"),
            fields={
                "from_group": {"type": "boolean", "label": "От имени сообщества", "default": True},
                "signed": {"type": "boolean", "label": "Добавить подпись автора", "default": False},
            },
            features={"scheduled_publish": True, "links": True},
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
            fields={
                "content_type": {
                    "type": "select",
                    "label": "Формат",
                    "options": [
                        "instagram_feed_image",
                        "instagram_video",
                        "instagram_carousel",
                        "instagram_story_image",
                        "instagram_story_video",
                    ],
                    "default": "instagram_feed_image",
                },
                "share_to_feed": {
                    "type": "boolean",
                    "label": "Показывать Reel в ленте",
                    "default": True,
                },
            },
            features={"carousel": True, "stories": True, "reels": True, "scheduled_publish": True},
            limits={"caption_chars": 2200},
        )
    )
    registry.register(
        LegacyPublisherAdapter(
            TikTokPublisher(settings),
            content_types=("video",),
            fields={
                "privacy_level": {
                    "type": "select",
                    "label": "Видимость",
                    "options": ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"],
                    "default": settings.tiktok_default_privacy_level,
                },
                "post_mode": {
                    "type": "select",
                    "label": "Режим публикации",
                    "options": ["DIRECT_POST", "DRAFT"],
                    "default": settings.tiktok_default_post_mode,
                },
                "disable_comment": {"type": "boolean", "label": "Отключить комментарии", "default": settings.tiktok_default_disable_comment},
                "disable_duet": {"type": "boolean", "label": "Отключить Duet", "default": settings.tiktok_default_disable_duet},
                "disable_stitch": {"type": "boolean", "label": "Отключить Stitch", "default": settings.tiktok_default_disable_stitch},
            },
            features={"video": True, "draft_upload": True, "scheduled_publish": True},
        )
    )
    return registry
