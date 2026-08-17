from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from autoposter_bot.models import MediaItem, PostJob, Target
from autoposter_bot.publishers.base import PublishResult, Publisher


SUPPORTED_CONTENT_TYPES = {
    "instagram_feed_image",
    "instagram_video",
    "instagram_carousel",
    "instagram_story_image",
    "instagram_story_video",
}


class InstagramPublisher(Publisher):
    platform = "instagram"

    def publish(self, job: PostJob, target: Target, dry_run: bool = False) -> PublishResult:
        if job.content_type not in SUPPORTED_CONTENT_TYPES:
            return PublishResult(
                self.platform,
                target.destination,
                False,
                f"Unsupported Instagram content_type: {job.content_type}",
            )
        missing = [key for key in ("access_token", "ig_user_id") if not target.options.get(key)]
        if missing:
            return PublishResult(
                self.platform,
                target.destination,
                False,
                f"Instagram account is missing required options: {', '.join(missing)}",
            )
        media_items = self._prepare_media_items(job.media_items, job.content_type)
        if not media_items:
            return PublishResult(self.platform, target.destination, False, "Instagram requires media URLs")
        if dry_run:
            return PublishResult(
                self.platform,
                target.destination,
                True,
                f"Dry run: {job.content_type} '{job.post_id}' with {len(media_items)} media item(s)",
            )

        import requests

        access_token = target.options["access_token"]
        flow = self._resolve_flow(target.options, access_token)
        api_version = target.options.get("graph_api_version", "v22.0")
        base_url = self._build_base_url(api_version, flow)
        ig_user_id = target.options["ig_user_id"]
        auth = self._build_auth(flow, access_token)

        creation_id: str | None = None
        try:
            creation_id = self._create_container(
                requests=requests,
                base_url=base_url,
                auth=auth,
                ig_user_id=ig_user_id,
                content_type=job.content_type,
                caption=job.text,
                media_items=media_items,
                flow=flow,
                options=target.options,
            )
            if job.content_type != "instagram_feed_image":
                self._wait_until_finished(requests, base_url, creation_id, auth)
            publish_id = self._publish_container(requests, base_url, ig_user_id, creation_id, auth, flow)
            return PublishResult(
                self.platform,
                target.destination,
                True,
                f"Instagram publish succeeded ({flow}): {publish_id}",
                external_post_id=str(publish_id),
                raw_response={
                    "creation_id": creation_id,
                    "publish_id": publish_id,
                    "flow": flow,
                    "content_type": job.content_type,
                },
            )
        except Exception as exc:
            return PublishResult(
                self.platform,
                target.destination,
                False,
                str(exc),
                raw_response={"creation_id": creation_id, "flow": flow},
            )

    def _resolve_flow(self, options: dict[str, Any], access_token: str) -> str:
        configured_flow = (options.get("api_flow") or options.get("auth_flow") or "").strip().lower()
        if configured_flow in {"instagram_login", "facebook_login"}:
            return configured_flow
        return "instagram_login" if access_token.startswith("IG") else "facebook_login"

    def _build_base_url(self, api_version: str, flow: str) -> str:
        host = "graph.instagram.com" if flow == "instagram_login" else "graph.facebook.com"
        return f"https://{host}/{api_version}"

    def _build_auth(self, flow: str, access_token: str) -> dict[str, dict[str, str]]:
        if flow == "instagram_login":
            return {
                "headers": {"Authorization": f"Bearer {access_token}"},
                "params": {},
                "data": {},
            }
        return {
            "headers": {},
            "params": {"access_token": access_token},
            "data": {"access_token": access_token},
        }

    def _media_edge(self, flow: str, ig_user_id: str) -> str:
        return "/me/media" if flow == "instagram_login" else f"/{ig_user_id}/media"

    def _media_publish_edge(self, flow: str, ig_user_id: str) -> str:
        return "/me/media_publish" if flow == "instagram_login" else f"/{ig_user_id}/media_publish"

    def _prepare_media_items(self, media_items: list[MediaItem], content_type: str) -> list[MediaItem]:
        prepared: list[MediaItem] = []
        for item in media_items:
            source = item.source
            if not item.is_remote:
                source = item.options.get("public_url", "")
            if not source:
                continue
            media_type = item.media_type
            if media_type == "auto":
                suffix = Path(item.source).suffix.lower()
                if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
                    media_type = "image"
                elif suffix in {".mp4", ".mov", ".mkv"}:
                    media_type = "video"
            prepared.append(MediaItem(source=source, media_type=media_type, order_index=item.order_index, options=item.options))

        if content_type in {"instagram_feed_image", "instagram_story_image"}:
            return [item for item in prepared if item.media_type == "image"][:1]
        if content_type in {"instagram_video", "instagram_story_video"}:
            return [item for item in prepared if item.media_type == "video"][:1]
        return prepared

    def _create_container(
        self,
        requests,
        base_url: str,
        auth: dict[str, dict[str, str]],
        ig_user_id: str,
        content_type: str,
        caption: str,
        media_items: list[MediaItem],
        flow: str,
        options: dict[str, Any],
    ) -> str:
        if content_type == "instagram_feed_image":
            payload = {"image_url": media_items[0].source, "caption": caption}
            return self._post_media(requests, base_url, ig_user_id, auth, payload, flow)
        if content_type == "instagram_video":
            share_to_feed = bool(options.get("share_to_feed", True))
            payload = {
                "media_type": "REELS",
                "video_url": media_items[0].source,
                "caption": caption,
                "share_to_feed": "true" if share_to_feed else "false",
            }
            return self._post_media(requests, base_url, ig_user_id, auth, payload, flow)
        if content_type == "instagram_story_image":
            payload = {"image_url": media_items[0].source, "media_type": "STORIES"}
            return self._post_media(requests, base_url, ig_user_id, auth, payload, flow)
        if content_type == "instagram_story_video":
            payload = {"video_url": media_items[0].source, "media_type": "STORIES"}
            return self._post_media(requests, base_url, ig_user_id, auth, payload, flow)
        if content_type == "instagram_carousel":
            child_ids = []
            for media_item in media_items:
                child_payload = {"is_carousel_item": "true"}
                if media_item.media_type == "video":
                    child_payload["video_url"] = media_item.source
                    child_payload["media_type"] = "VIDEO"
                else:
                    child_payload["image_url"] = media_item.source
                child_ids.append(self._post_media(requests, base_url, ig_user_id, auth, child_payload, flow))
            payload = {
                "media_type": "CAROUSEL",
                "children": ",".join(child_ids),
                "caption": caption,
            }
            return self._post_media(requests, base_url, ig_user_id, auth, payload, flow)
        raise ValueError(f"Unsupported Instagram content_type: {content_type}")

    def _post_media(
        self,
        requests,
        base_url: str,
        ig_user_id: str,
        auth: dict[str, dict[str, str]],
        payload: dict,
        flow: str,
    ) -> str:
        response = requests.post(
            f"{base_url}{self._media_edge(flow, ig_user_id)}",
            headers=auth["headers"],
            params=auth["params"],
            data={**payload, **auth["data"]},
            timeout=180,
        )
        data = response.json()
        if "id" not in data:
            raise RuntimeError(f"Instagram media create failed: {data}")
        return data["id"]

    def _wait_until_finished(self, requests, base_url: str, creation_id: str, auth: dict[str, dict[str, str]]) -> None:
        for _ in range(20):
            response = requests.get(
                f"{base_url}/{creation_id}",
                headers=auth["headers"],
                params={"fields": "status_code", **auth["params"]},
                timeout=60,
            )
            data = response.json()
            status = data.get("status_code")
            if status == "FINISHED":
                return
            if status == "ERROR":
                raise RuntimeError(f"Instagram media processing failed: {data}")
            time.sleep(5)
        raise RuntimeError("Instagram media processing timed out")

    def _publish_container(
        self,
        requests,
        base_url: str,
        ig_user_id: str,
        creation_id: str,
        auth: dict[str, dict[str, str]],
        flow: str,
    ) -> str:
        response = requests.post(
            f"{base_url}{self._media_publish_edge(flow, ig_user_id)}",
            headers=auth["headers"],
            params=auth["params"],
            data={"creation_id": creation_id, **auth["data"]},
            timeout=120,
        )
        data = response.json()
        if "id" not in data:
            raise RuntimeError(f"Instagram media_publish failed: {data}")
        return data["id"]
