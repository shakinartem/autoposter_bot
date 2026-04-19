from __future__ import annotations

from pathlib import Path

from autoposter_bot.models import MediaItem, PostJob, Target
from autoposter_bot.publishers.base import PublishResult, Publisher


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi"}


class VkPublisher(Publisher):
    platform = "vk"

    def __init__(self, token: str | None, api_version: str) -> None:
        self.token = token
        self.api_version = api_version

    def publish(self, job: PostJob, target: Target, dry_run: bool = False) -> PublishResult:
        owner_id = target.destination
        if not owner_id:
            return PublishResult(self.platform, None, False, "VK owner_id is not configured in account")
        if dry_run:
            return PublishResult(
                self.platform,
                owner_id,
                True,
                f"Dry run: {job.content_type} '{job.post_id}' -> {target.account_name or owner_id}",
            )
        if not self.token:
            return PublishResult(self.platform, owner_id, False, "VK token is not configured")

        import requests

        attachments: list[str] = []
        for media_item in self._normalize_media(job.media_items):
            if media_item.media_type == "image":
                attachments.append(self._upload_photo(requests, owner_id, Path(media_item.source)))
            elif media_item.media_type == "video":
                attachments.append(self._upload_video(requests, owner_id, Path(media_item.source), job.text))
            else:
                return PublishResult(self.platform, owner_id, False, f"Unsupported VK media type: {media_item.media_type}")

        payload = {
            "owner_id": owner_id,
            "from_group": 1 if str(owner_id).startswith("-") else 0,
            "message": job.text,
            "attachments": ",".join(filter(None, attachments)) or target.options.get("attachment"),
            "access_token": self.token,
            "v": self.api_version,
        }
        response = requests.post("https://api.vk.com/method/wall.post", data=payload, timeout=60)
        data = response.json()
        if "response" in data:
            return PublishResult(self.platform, owner_id, True, "VK publish succeeded")
        return PublishResult(self.platform, owner_id, False, f"VK API error: {data}")

    def _upload_photo(self, requests, owner_id: str, media_path) -> str:
        server_response = requests.post(
            "https://api.vk.com/method/photos.getWallUploadServer",
            data={
                "owner_id": owner_id,
                "access_token": self.token,
                "v": self.api_version,
            },
            timeout=60,
        ).json()
        self._assert_vk_response(server_response, "photos.getWallUploadServer")
        upload_url = server_response["response"]["upload_url"]
        with media_path.open("rb") as media_stream:
            uploaded = requests.post(upload_url, files={"photo": media_stream}, timeout=180).json()
        saved = requests.post(
            "https://api.vk.com/method/photos.saveWallPhoto",
            data={
                "owner_id": owner_id,
                "photo": uploaded["photo"],
                "server": uploaded["server"],
                "hash": uploaded["hash"],
                "access_token": self.token,
                "v": self.api_version,
            },
            timeout=60,
        ).json()
        self._assert_vk_response(saved, "photos.saveWallPhoto")
        item = saved["response"][0]
        return f"photo{item['owner_id']}_{item['id']}"

    def _upload_video(self, requests, owner_id: str, media_path, description: str) -> str:
        payload = {
            "name": media_path.stem,
            "description": description[:5000],
            "wallpost": 0,
            "access_token": self.token,
            "v": self.api_version,
        }
        if str(owner_id).startswith("-"):
            payload["group_id"] = owner_id.lstrip("-")

        save_response = requests.post(
            "https://api.vk.com/method/video.save",
            data=payload,
            timeout=60,
        ).json()
        self._assert_vk_response(save_response, "video.save")
        upload_info = save_response["response"]
        with media_path.open("rb") as media_stream:
            requests.post(upload_info["upload_url"], files={"video_file": media_stream}, timeout=600)
        return f"video{upload_info['owner_id']}_{upload_info['video_id']}"

    def _assert_vk_response(self, data: dict, method_name: str) -> None:
        if "response" in data:
            return
        if "error" in data:
            error = data["error"]
            code = error.get("error_code")
            message = error.get("error_msg")
            raise RuntimeError(f"VK {method_name} failed [{code}]: {message}")
        raise RuntimeError(f"VK {method_name} returned unexpected payload: {data}")

    def _normalize_media(self, media_items: list[MediaItem]) -> list[MediaItem]:
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
