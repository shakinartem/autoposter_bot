from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from autoposter_bot.models import MediaItem, PostJob, Target
from autoposter_bot.publishers.base import PublishResult, Publisher


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi"}


class TelegramPublisher(Publisher):
    platform = "telegram"

    def __init__(self, bot_token: str | None) -> None:
        self.bot_token = bot_token
        self.http = None

    def publish(self, job: PostJob, target: Target, dry_run: bool = False) -> PublishResult:
        chat_id = target.destination
        if not chat_id:
            return PublishResult(self.platform, None, False, "Telegram chat_id is not configured in account")
        if dry_run:
            return PublishResult(
                self.platform,
                chat_id,
                True,
                f"Dry run: {job.content_type} '{job.post_id}' -> {target.account_name or chat_id}",
            )
        if not self.bot_token:
            return PublishResult(self.platform, chat_id, False, "Telegram bot token is not configured")

        import requests

        if self.http is None:
            self.http = requests.Session()

        media_items = self._normalize_media_types(job.media_items)
        base_url = f"https://api.telegram.org/bot{self.bot_token}"
        options = self._message_options(target.options)
        try:
            if not media_items:
                response = self.http.post(
                    f"{base_url}/sendMessage",
                    data={"chat_id": chat_id, "text": job.text, **options},
                    timeout=(20, 180),
                )
                return self._build_result(chat_id, response)

            if len(media_items) == 1:
                return self._publish_single_media(base_url, chat_id, media_items[0], job.text, options)
            return self._publish_media_group(base_url, chat_id, media_items, job.text, options)
        except requests.exceptions.RequestException as exc:
            # A network exception after POST has an ambiguous outcome. The
            # centralized worker must reconcile it instead of blindly retrying.
            return PublishResult(
                self.platform,
                chat_id,
                False,
                f"Telegram network error: {exc}",
                error_code="unknown_publish_outcome",
                retryable=False,
                raw_response={"exception_type": type(exc).__name__},
            )

    def _publish_single_media(
        self,
        base_url: str,
        chat_id: str,
        media_item: MediaItem,
        caption: str,
        options: dict[str, Any],
    ) -> PublishResult:
        media_type = media_item.media_type
        if media_type == "image":
            method = "sendPhoto"
            field_name = "photo"
        elif media_type == "video":
            method = "sendVideo"
            field_name = "video"
        else:
            return PublishResult(self.platform, chat_id, False, f"Unsupported Telegram media type: {media_type}")

        with Path(media_item.source).open("rb") as media_stream:
            response = self.http.post(
                f"{base_url}/{method}",
                data={"chat_id": chat_id, "caption": caption, **options},
                files={field_name: media_stream},
                timeout=(20, 300),
            )
        return self._build_result(chat_id, response)

    def _publish_media_group(
        self,
        base_url: str,
        chat_id: str,
        media_items: list[MediaItem],
        caption: str,
        options: dict[str, Any],
    ) -> PublishResult:
        files = {}
        media_payload = []
        opened_streams = []
        try:
            for index, media_item in enumerate(media_items):
                attachment_name = f"file{index}"
                media_stream = Path(media_item.source).open("rb")
                opened_streams.append(media_stream)
                files[attachment_name] = media_stream
                media_object: dict[str, Any] = {
                    "type": "photo" if media_item.media_type == "image" else "video",
                    "media": f"attach://{attachment_name}",
                }
                if index == 0 and caption:
                    media_object["caption"] = caption
                    if options.get("parse_mode"):
                        media_object["parse_mode"] = options["parse_mode"]
                media_payload.append(media_object)

            group_options = {
                key: value
                for key, value in options.items()
                if key in {"disable_notification", "protect_content"}
            }
            response = self.http.post(
                f"{base_url}/sendMediaGroup",
                data={
                    "chat_id": chat_id,
                    "media": json.dumps(media_payload, ensure_ascii=False),
                    **group_options,
                },
                files=files,
                timeout=(20, 360),
            )
            return self._build_result(chat_id, response)
        finally:
            for stream in opened_streams:
                stream.close()

    @staticmethod
    def _message_options(raw: dict[str, Any]) -> dict[str, Any]:
        options: dict[str, Any] = {}
        parse_mode = str(raw.get("parse_mode") or "").strip()
        if parse_mode and parse_mode.lower() != "plain":
            options["parse_mode"] = parse_mode
        if "disable_notification" in raw:
            options["disable_notification"] = bool(raw["disable_notification"])
        if "protect_content" in raw:
            options["protect_content"] = bool(raw["protect_content"])
        return options

    def _normalize_media_types(self, media_items: list[MediaItem]) -> list[MediaItem]:
        normalized: list[MediaItem] = []
        for item in media_items:
            if item.is_remote:
                continue
            path = Path(item.source)
            if not path.exists():
                continue
            if item.media_type in {"image", "video"}:
                normalized.append(MediaItem(item.source, item.media_type, item.order_index, item.options))
                continue
            suffix = path.suffix.lower()
            if suffix in IMAGE_EXTENSIONS:
                normalized.append(MediaItem(item.source, "image", item.order_index, item.options))
            elif suffix in VIDEO_EXTENSIONS:
                normalized.append(MediaItem(item.source, "video", item.order_index, item.options))
        return normalized

    def _build_result(self, destination: str, response) -> PublishResult:
        try:
            payload = response.json()
        except Exception:
            payload = {"raw_text": response.text}

        if response.ok and isinstance(payload, dict) and payload.get("ok", True):
            result = payload.get("result")
            messages = result if isinstance(result, list) else [result]
            message_ids = [
                str(item["message_id"])
                for item in messages
                if isinstance(item, dict) and item.get("message_id") is not None
            ]
            remote_id = message_ids[0] if message_ids else None
            external_url = None
            if remote_id and destination.startswith("@"):
                external_url = f"https://t.me/{destination.lstrip('@')}/{remote_id}"
            return PublishResult(
                self.platform,
                destination,
                True,
                "Telegram publish succeeded",
                external_post_id=remote_id,
                external_url=external_url,
                raw_response={"telegram": payload, "message_ids": message_ids},
            )

        retry_after = None
        if isinstance(payload, dict):
            parameters = payload.get("parameters") or {}
            if isinstance(parameters, dict):
                raw_retry = parameters.get("retry_after")
                try:
                    retry_after = int(raw_retry) if raw_retry is not None else None
                except (TypeError, ValueError):
                    retry_after = None
        rate_limit_reset_at = (
            datetime.now() + timedelta(seconds=retry_after) if retry_after is not None else None
        )
        retryable = response.status_code == 429 or 500 <= response.status_code < 600
        error_code = "rate_limited" if response.status_code == 429 else f"telegram_http_{response.status_code}"
        return PublishResult(
            self.platform,
            destination,
            False,
            f"Telegram API error {response.status_code}: {response.text}",
            raw_response={"telegram": payload},
            error_code=error_code,
            retryable=retryable,
            rate_limit_reset_at=rate_limit_reset_at,
        )
