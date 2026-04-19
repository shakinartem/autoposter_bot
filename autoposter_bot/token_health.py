from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import quote

from autoposter_bot.config import Settings
from autoposter_bot.notifier import TelegramNotifier


@dataclass(slots=True)
class TokenStatus:
    provider: str
    ok: bool
    detail: str


def check_vk_token_status(settings: Settings, warning_hours: int = 6) -> TokenStatus:
    if not settings.vk_token:
        return TokenStatus("vk", False, "VK_TOKEN is empty")
    if not settings.vk_token_expires_at:
        return TokenStatus("vk", True, "VK token is set, expiration date is not configured")

    try:
        expires_at = datetime.fromisoformat(settings.vk_token_expires_at)
    except ValueError:
        return TokenStatus("vk", False, "VK_TOKEN_EXPIRES_AT has invalid ISO datetime format")

    now = datetime.now()
    delta = expires_at - now
    if delta.total_seconds() <= 0:
        return TokenStatus("vk", False, f"VK token expired at {expires_at.isoformat(sep=' ')}")
    if delta <= timedelta(hours=warning_hours):
        return TokenStatus(
            "vk",
            False,
            f"VK token expires soon at {expires_at.isoformat(sep=' ')}; renew it in the same browser/network",
        )
    return TokenStatus("vk", True, f"VK token is valid until {expires_at.isoformat(sep=' ')}")


def send_vk_token_warning_if_needed(settings: Settings, warning_hours: int = 6) -> TokenStatus:
    status = check_vk_token_status(settings, warning_hours=warning_hours)
    if status.ok:
        return status
    if "expires soon" not in status.detail and "expired" not in status.detail:
        return status

    notifier = TelegramNotifier(settings)
    if not notifier.is_configured():
        return TokenStatus("vk", False, f"{status.detail}; TOKEN_WARNING_CHAT_ID is not configured")

    notifier.send(
        "VK token warning:\n"
        f"{status.detail}\n"
        "Recommended action: renew the VK token in the same browser and from the same network/IP you use for the bot."
    )
    return TokenStatus("vk", status.ok, f"{status.detail}; warning sent to Telegram")


def build_vk_token_warning_message(settings: Settings, warning_hours: int = 1) -> str | None:
    status = check_vk_token_status(settings, warning_hours=warning_hours)
    if status.ok:
        return None
    if "expires soon" not in status.detail and "expired" not in status.detail:
        return None
    return (
        "VK token warning:\n"
        f"{status.detail}\n"
        "Recommended action: renew the VK token in the same browser and from the same network/IP you use for the bot.\n"
        f"Auth link: {build_vk_oauth_link(settings) or 'configure VK_CLIENT_ID and VK_REDIRECT_URI in .env'}"
    )


def check_instagram_token_status(settings: Settings, warning_hours: int = 24) -> TokenStatus:
    if not settings.instagram_access_token:
        return TokenStatus("instagram", False, "INSTAGRAM_ACCESS_TOKEN is empty")
    if not settings.instagram_token_expires_at:
        return TokenStatus("instagram", True, "Instagram token is set, expiration date is not configured")
    try:
        expires_at = datetime.fromisoformat(settings.instagram_token_expires_at)
    except ValueError:
        return TokenStatus("instagram", False, "INSTAGRAM_TOKEN_EXPIRES_AT has invalid ISO datetime format")

    now = datetime.now()
    delta = expires_at - now
    if delta.total_seconds() <= 0:
        return TokenStatus("instagram", False, f"Instagram token expired at {expires_at.isoformat(sep=' ')}")
    if delta <= timedelta(hours=warning_hours):
        return TokenStatus(
            "instagram",
            False,
            f"Instagram token expires soon at {expires_at.isoformat(sep=' ')}; renew it via Meta OAuth",
        )
    return TokenStatus("instagram", True, f"Instagram token is valid until {expires_at.isoformat(sep=' ')}")


def build_instagram_token_warning_message(settings: Settings, warning_hours: int = 24) -> str | None:
    status = check_instagram_token_status(settings, warning_hours=warning_hours)
    if status.ok:
        return None
    if "expires soon" not in status.detail and "expired" not in status.detail:
        return None
    return (
        "Instagram token warning:\n"
        f"{status.detail}\n"
        f"Auth link: {build_instagram_oauth_link(settings) or 'configure INSTAGRAM_APP_ID and INSTAGRAM_REDIRECT_URI in .env'}"
    )


def build_vk_oauth_link(settings: Settings) -> str | None:
    client_id = settings.vk_client_id or "6287487"
    redirect_uri = settings.vk_redirect_uri or "https://oauth.vk.com/blank.html"
    scope = settings.vk_scope or "wall,photos,groups,offline"
    return (
        "https://oauth.vk.com/authorize"
        f"?client_id={client_id}"
        "&display=page"
        f"&redirect_uri={redirect_uri}"
        f"&scope={scope}"
        "&response_type=token"
        "&v=5.199"
    )


def build_instagram_oauth_link(settings: Settings) -> str | None:
    if not settings.instagram_app_id or not settings.instagram_redirect_uri:
        return None
    encoded_scope = settings.instagram_scope.replace(",", "%2C")
    return (
        "https://www.instagram.com/oauth/authorize"
        "?force_reauth=true"
        f"&client_id={settings.instagram_app_id}"
        f"&redirect_uri={settings.instagram_redirect_uri}"
        "&response_type=code"
        f"&scope={encoded_scope}"
    )
