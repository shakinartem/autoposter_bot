from __future__ import annotations

from typing import Any


ACCOUNT_CONNECTION_SPECS: dict[str, dict[str, Any]] = {
    "telegram": {
        "platform": "telegram",
        "title": "Telegram",
        "destination": {
            "label": "Channel / chat ID",
            "placeholder": "@channel or -100…",
            "required": True,
        },
        "fields": {},
        "notes": "Autoposter uses the configured system bot. Add it as an administrator of the target channel first.",
    },
    "vk": {
        "platform": "vk",
        "title": "VK",
        "destination": {
            "label": "Wall owner ID",
            "placeholder": "-123456 for a community",
            "required": True,
        },
        "fields": {
            "access_token": {
                "type": "secret",
                "label": "Access token",
                "required": True,
                "placeholder": "vk1.a.…",
            },
        },
    },
    "instagram": {
        "platform": "instagram",
        "title": "Instagram",
        "destination": {
            "label": "Account label / username",
            "placeholder": "@brand",
            "required": False,
        },
        "fields": {
            "ig_user_id": {
                "type": "text",
                "label": "Instagram user ID",
                "required": True,
            },
            "access_token": {
                "type": "secret",
                "label": "Access token",
                "required": True,
            },
            "api_flow": {
                "type": "select",
                "label": "API login flow",
                "required": False,
                "options": ["instagram_login", "facebook_login"],
                "default": "instagram_login",
            },
        },
    },
    "tiktok": {
        "platform": "tiktok",
        "title": "TikTok",
        "destination": {
            "label": "Account label / username",
            "placeholder": "@brand",
            "required": False,
        },
        "fields": {
            "access_token": {
                "type": "secret",
                "label": "Access token",
                "required": True,
            },
            "refresh_token": {
                "type": "secret",
                "label": "Refresh token",
                "required": False,
            },
            "open_id": {
                "type": "text",
                "label": "Open ID",
                "required": False,
            },
        },
    },
}


def get_account_connection_specs(platforms: tuple[str, ...] | list[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for platform in platforms:
        key = platform.lower()
        spec = ACCOUNT_CONNECTION_SPECS.get(key)
        if spec:
            result[key] = spec
    return result
