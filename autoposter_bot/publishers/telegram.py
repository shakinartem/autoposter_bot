from __future__ import annotations

import json
import time
from pathlib import Path

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
        try:
            if not media_items:
                response = self._post_with_retries(
                    f"{base_url}/sendMessage",
                    data={"chat_id": chat_id, "text": job.text},
                    timeout=(20, 180),
                )
                return self._build_result(chat_id, response)

            if len(media_items) == 1:
                return self._publish_single_media(base_url, chat_id, media_items[0], job.text)
            return self._publish_media_group(base_url, chat_id, media_items, job.text)
        except requests.exceptions.RequestException as exc:
            return PublishResult(self.platform, chat_id, False, f"Telegram network error: {exc}")

    def _publish_single_media(self, base_url: str, chat_id: str, media_item: MediaItem, caption: str) -> PublishResult:
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
            response = self._post_with_retries(
                f"{base_url}/{method}",
                data={"chat_id": chat_id, "caption": caption},
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
                media_object = {
                    "type": "photo" if media_item.media_type == "image" else "video",
                    "media": f"attach://{attachment_name}",
                }
                if index == 0 and caption:
                    media_object["caption"] = caption
                media_payload.append(media_object)

            response = self._post_with_retries(
                f"{base_url}/sendMediaGroup",
                data={"chat_id": chat_id, "media": json.dumps(media_payload, ensure_ascii=False)},
                files=files,
                timeout=(20, 360),
            )
            return self._build_result(chat_id, response)
        finally:
            for stream in opened_streams:
                stream.close()

    def _post_with_retries(self, url: str, **kwargs):
        last_error = None
        for attempt in range(1, 4):
            try:
                return self.http.post(url, **kwargs)
            except Exception as exc:
                last_error = exc
                if attempt == 3:
                    break
                time.sleep(attempt)
        raise last_error

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
        if response.ok:
            return PublishResult(self.platform, destination, True, "Telegram publish succeeded")
        return PublishResult(
            self.platform,
            destination,
            False,
            f"Telegram API error {response.status_code}: {response.text}",
        )
