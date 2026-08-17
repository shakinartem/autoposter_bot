from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import dotenv_values, load_dotenv
except ModuleNotFoundError:
    def dotenv_values(_env_file: str | None = None) -> dict[str, str]:
        return {}

    def load_dotenv(_env_file: str | None = None) -> bool:
        return False


@dataclass(slots=True)
class Settings:
    env_file_path: Path
    telegram_bot_token: str | None
    telegram_bot_username: str | None
    telegram_default_destination: str | None
    telegram_admin_user_ids: list[int]
    telegram_oidc_client_id: str | None
    telegram_oidc_client_secret: str | None
    telegram_oidc_redirect_uri: str | None
    telegram_oidc_scope: str
    vk_token: str | None
    vk_api_version: str
    vk_default_owner_id: str | None
    vk_token_expires_at: str | None
    vk_token_obtained_at: str | None
    vk_token_lifetime_seconds: int | None
    vk_client_id: str | None
    vk_redirect_uri: str | None
    vk_scope: str
    token_warning_chat_id: str | None
    instagram_graph_api_version: str
    instagram_ig_user_id: str | None
    instagram_access_token: str | None
    instagram_username: str | None
    instagram_token_expires_at: str | None
    instagram_token_obtained_at: str | None
    instagram_token_lifetime_seconds: int | None
    instagram_app_id: str | None
    instagram_app_secret: str | None
    instagram_redirect_uri: str | None
    instagram_scope: str
    tiktok_access_token: str | None
    tiktok_username: str | None
    tiktok_token_expires_at: str | None
    tiktok_token_obtained_at: str | None
    tiktok_token_lifetime_seconds: int | None
    tiktok_client_key: str | None
    tiktok_client_secret: str | None
    tiktok_redirect_uri: str | None
    tiktok_scope: str
    tiktok_default_privacy_level: str
    tiktok_default_post_mode: str
    tiktok_default_disable_comment: bool
    tiktok_default_disable_duet: bool
    tiktok_default_disable_stitch: bool
    cloudinary_cloud_name: str | None
    cloudinary_api_key: str | None
    cloudinary_api_secret: str | None
    cloudinary_folder: str | None
    yookassa_shop_id: str | None
    yookassa_secret_key: str | None
    yookassa_return_url: str | None
    yookassa_webhook_url: str | None
    yookassa_currency: str
    yookassa_test_mode: bool
    database_path: Path
    queue_dir: Path


def load_settings(env_file: str | None = None) -> Settings:
    env_file_path = Path(env_file or ".env").resolve()
    load_dotenv(env_file)
    file_values = {
        key: value
        for key, value in dotenv_values(str(env_file_path)).items()
        if value is not None
    }

    def get_value(key: str, default: str | None = None) -> str | None:
        if key in file_values:
            return file_values[key]
        return os.getenv(key, default)

    queue_dir = Path(get_value("AUTOPOSTER_QUEUE_DIR", "queue") or "queue").resolve()
    database_path = Path(get_value("AUTOPOSTER_DB_PATH", "data/autoposter.sqlite3") or "data/autoposter.sqlite3").resolve()
    admin_ids = _parse_int_list(get_value("TELEGRAM_ADMIN_USER_IDS", "") or "")
    return Settings(
        env_file_path=env_file_path,
        telegram_bot_token=get_value("TELEGRAM_BOT_TOKEN"),
        telegram_bot_username=get_value("TELEGRAM_BOT_USERNAME"),
        telegram_default_destination=get_value("TELEGRAM_DEFAULT_DESTINATION"),
        telegram_admin_user_ids=admin_ids,
        telegram_oidc_client_id=get_value("TELEGRAM_OIDC_CLIENT_ID"),
        telegram_oidc_client_secret=get_value("TELEGRAM_OIDC_CLIENT_SECRET"),
        telegram_oidc_redirect_uri=get_value("TELEGRAM_OIDC_REDIRECT_URI"),
        telegram_oidc_scope=get_value("TELEGRAM_OIDC_SCOPE", "openid profile") or "openid profile",
        vk_token=get_value("VK_TOKEN"),
        vk_api_version=get_value("VK_API_VERSION", "5.199") or "5.199",
        vk_default_owner_id=get_value("VK_DEFAULT_OWNER_ID"),
        vk_token_expires_at=get_value("VK_TOKEN_EXPIRES_AT"),
        vk_token_obtained_at=get_value("VK_TOKEN_OBTAINED_AT"),
        vk_token_lifetime_seconds=_parse_int(get_value("VK_TOKEN_LIFETIME_SECONDS")),
        vk_client_id=get_value("VK_CLIENT_ID"),
        vk_redirect_uri=get_value("VK_REDIRECT_URI"),
        vk_scope=get_value("VK_SCOPE", "wall,photos,video,offline,groups") or "wall,photos,video,offline,groups",
        token_warning_chat_id=get_value("TOKEN_WARNING_CHAT_ID") or get_value("TELEGRAM_DEFAULT_DESTINATION"),
        instagram_graph_api_version=get_value("INSTAGRAM_GRAPH_API_VERSION", "v22.0") or "v22.0",
        instagram_ig_user_id=get_value("INSTAGRAM_IG_USER_ID"),
        instagram_access_token=get_value("INSTAGRAM_ACCESS_TOKEN"),
        instagram_username=get_value("INSTAGRAM_USERNAME"),
        instagram_token_expires_at=get_value("INSTAGRAM_TOKEN_EXPIRES_AT"),
        instagram_token_obtained_at=get_value("INSTAGRAM_TOKEN_OBTAINED_AT"),
        instagram_token_lifetime_seconds=_parse_int(get_value("INSTAGRAM_TOKEN_LIFETIME_SECONDS")),
        instagram_app_id=get_value("INSTAGRAM_APP_ID"),
        instagram_app_secret=get_value("INSTAGRAM_APP_SECRET"),
        instagram_redirect_uri=get_value("INSTAGRAM_REDIRECT_URI"),
        instagram_scope=get_value(
            "INSTAGRAM_SCOPE",
            "instagram_business_basic,instagram_business_content_publish",
        ) or "instagram_business_basic,instagram_business_content_publish",
        tiktok_access_token=get_value("TIKTOK_ACCESS_TOKEN"),
        tiktok_username=get_value("TIKTOK_USERNAME"),
        tiktok_token_expires_at=get_value("TIKTOK_TOKEN_EXPIRES_AT"),
        tiktok_token_obtained_at=get_value("TIKTOK_TOKEN_OBTAINED_AT"),
        tiktok_token_lifetime_seconds=_parse_int(get_value("TIKTOK_TOKEN_LIFETIME_SECONDS")),
        tiktok_client_key=get_value("TIKTOK_CLIENT_KEY"),
        tiktok_client_secret=get_value("TIKTOK_CLIENT_SECRET"),
        tiktok_redirect_uri=get_value("TIKTOK_REDIRECT_URI"),
        tiktok_scope=get_value(
            "TIKTOK_SCOPE",
            "user.info.basic,video.publish,video.upload",
        ) or "user.info.basic,video.publish,video.upload",
        tiktok_default_privacy_level=get_value("TIKTOK_DEFAULT_PRIVACY_LEVEL", "SELF_ONLY") or "SELF_ONLY",
        tiktok_default_post_mode=get_value("TIKTOK_DEFAULT_POST_MODE", "DIRECT_POST") or "DIRECT_POST",
        tiktok_default_disable_comment=_parse_bool(get_value("TIKTOK_DEFAULT_DISABLE_COMMENT", "false") or "false"),
        tiktok_default_disable_duet=_parse_bool(get_value("TIKTOK_DEFAULT_DISABLE_DUET", "false") or "false"),
        tiktok_default_disable_stitch=_parse_bool(get_value("TIKTOK_DEFAULT_DISABLE_STITCH", "false") or "false"),
        cloudinary_cloud_name=get_value("CLOUDINARY_CLOUD_NAME"),
        cloudinary_api_key=get_value("CLOUDINARY_API_KEY"),
        cloudinary_api_secret=get_value("CLOUDINARY_API_SECRET"),
        cloudinary_folder=get_value("CLOUDINARY_FOLDER", "autoposter"),
        yookassa_shop_id=get_value("YOOKASSA_SHOP_ID"),
        yookassa_secret_key=get_value("YOOKASSA_SECRET_KEY"),
        yookassa_return_url=get_value("YOOKASSA_RETURN_URL"),
        yookassa_webhook_url=get_value("YOOKASSA_WEBHOOK_URL"),
        yookassa_currency=get_value("YOOKASSA_CURRENCY", "RUB") or "RUB",
        yookassa_test_mode=_parse_bool(get_value("YOOKASSA_TEST_MODE", "false") or "false"),
        database_path=database_path,
        queue_dir=queue_dir,
    )


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_int_list(value: str) -> list[int]:
    items = []
    for raw_item in value.split(","):
        raw_item = raw_item.strip()
        if not raw_item:
            continue
        items.append(int(raw_item))
    return items


def _parse_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    return int(value)
