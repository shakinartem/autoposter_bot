from __future__ import annotations

import time
from math import ceil
from pathlib import Path
from urllib.parse import urlparse
from typing import Callable

from autoposter_bot.config import Settings
from autoposter_bot.models import MediaItem, PostJob, Target
from autoposter_bot.publishers.base import PublishResult, Publisher


class TikTokPublisher(Publisher):
    platform = "tiktok"
    INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
    CREATOR_INFO_URL = "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"
    STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings

    def publish(
        self,
        job: PostJob,
        target: Target,
        dry_run: bool = False,
        *,
        progress_callback: Callable[[PublishResult], None] | None = None,
    ) -> PublishResult:
        if job.content_type != "tiktok_video":
            return PublishResult(
                self.platform,
                target.destination,
                False,
                f"TikTok publisher expects content_type=tiktok_video, got {job.content_type}",
            )
        if len(job.media_items) != 1:
            return PublishResult(self.platform, target.destination, False, "TikTok expects exactly one media item")
        missing = [key for key in ("access_token",) if not target.options.get(key)]
        if missing:
            return PublishResult(
                self.platform,
                target.destination,
                False,
                f"TikTok account is missing required options: {', '.join(missing)}",
            )
        media_item = self._normalize_media(job.media_items[0])
        if not media_item or media_item.media_type != "video":
            return PublishResult(self.platform, target.destination, False, "TikTok requires a video file or video URL")
        if dry_run:
            return PublishResult(
                self.platform,
                target.destination,
                True,
                f"Dry run: tiktok_video '{job.post_id}' -> {target.account_name or target.destination}",
            )

        import requests

        access_token = target.options["access_token"]
        try:
            creator_info = self._query_creator_info(requests, access_token)
            post_mode = target.options.get("post_mode", "DIRECT_POST")
            requested_privacy_level = target.options.get("privacy_level") or (
                self.settings.tiktok_default_privacy_level
                if self.settings and self.settings.tiktok_default_privacy_level
                else "SELF_ONLY"
            )
            privacy_level = self._resolve_privacy_level(requested_privacy_level, creator_info)
            disable_comment = self._resolve_interaction_flag(
                target.options.get("disable_comment", False),
                creator_info.get("comment_disabled"),
            )
            disable_duet = self._resolve_interaction_flag(
                target.options.get("disable_duet", False),
                creator_info.get("duet_disabled"),
            )
            disable_stitch = self._resolve_interaction_flag(
                target.options.get("disable_stitch", False),
                creator_info.get("stitch_disabled"),
            )
            if media_item.is_remote:
                publish_id = self._publish_from_url(
                    requests=requests,
                    access_token=access_token,
                    video_url=media_item.source,
                    title=job.text,
                    post_mode=post_mode,
                    privacy_level=privacy_level,
                    disable_comment=disable_comment,
                    disable_duet=disable_duet,
                    disable_stitch=disable_stitch,
                    progress_callback=progress_callback,
                    destination=target.destination,
                )
            else:
                publish_id = self._upload_local_file(
                    requests=requests,
                    access_token=access_token,
                    video_path=Path(media_item.source),
                    title=job.text,
                    post_mode=post_mode,
                    privacy_level=privacy_level,
                    disable_comment=disable_comment,
                    disable_duet=disable_duet,
                    disable_stitch=disable_stitch,
                    progress_callback=progress_callback,
                    destination=target.destination,
                )
            return PublishResult(
                self.platform,
                target.destination,
                True,
                f"TikTok publish initialized: {publish_id} (privacy={privacy_level})",
                provider_tracking_id=publish_id,
                raw_response={
                    "publish_id": publish_id,
                    "privacy_level": privacy_level,
                    "post_mode": post_mode,
                },
                status="processing",
            )
        except Exception as exc:
            return PublishResult(self.platform, target.destination, False, str(exc))

    def fetch_status(self, tracking_id: str, target: Target) -> PublishResult:
        access_token = str(target.options.get("access_token") or "").strip()
        if not access_token:
            return PublishResult(
                self.platform,
                target.destination,
                False,
                "TikTok account access_token is not configured",
                provider_tracking_id=tracking_id or None,
                error_code="invalid_token",
                retryable=False,
                status="failed",
            )
        if not tracking_id:
            return PublishResult(
                self.platform,
                target.destination,
                False,
                "TikTok publish_id is required for status reconciliation",
                error_code="tracking_id_missing",
                retryable=False,
                status="failed",
            )

        import requests

        try:
            response = requests.post(
                self.STATUS_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json; charset=UTF-8",
                },
                json={"publish_id": tracking_id},
                timeout=60,
            )
        except requests.exceptions.RequestException as exc:
            return PublishResult(
                self.platform,
                target.destination,
                False,
                f"TikTok status fetch network error: {exc}",
                provider_tracking_id=tracking_id,
                error_code="status_check_transient",
                retryable=True,
                status="status_check_failed",
            )

        try:
            payload = response.json()
        except ValueError:
            payload = {"raw_text": response.text}
        error = payload.get("error") if isinstance(payload, dict) else {}
        error = error if isinstance(error, dict) else {}
        error_code = str(error.get("code") or "")
        if not response.ok or error_code not in {"", "ok"}:
            transient = response.status_code == 429 or response.status_code >= 500 or error_code in {
                "rate_limit_exceeded",
                "internal_error",
            }
            return PublishResult(
                self.platform,
                target.destination,
                False,
                f"TikTok status fetch failed: {payload}",
                provider_tracking_id=tracking_id,
                raw_response=payload if isinstance(payload, dict) else {},
                error_code="status_check_transient" if transient else (error_code or "status_check_failed"),
                retryable=transient,
                status="status_check_failed",
            )

        data = payload.get("data") or {}
        status = str(data.get("status") or "").upper()
        raw = payload if isinstance(payload, dict) else {}
        if status in {"PROCESSING_UPLOAD", "PROCESSING_DOWNLOAD", "SEND_TO_USER_INBOX"}:
            return PublishResult(
                self.platform,
                target.destination,
                True,
                f"TikTok publish is still processing: {status}",
                provider_tracking_id=tracking_id,
                raw_response=raw,
                status="processing",
            )
        if status == "PUBLISH_COMPLETE":
            public_ids = data.get("publicaly_available_post_id") or []
            external_post_id = str(public_ids[0]) if public_ids else None
            return PublishResult(
                self.platform,
                target.destination,
                True,
                "TikTok publish completed",
                external_post_id=external_post_id,
                provider_tracking_id=tracking_id,
                raw_response=raw,
                status="published",
            )
        if status == "FAILED":
            fail_reason = str(data.get("fail_reason") or "publish_failed")
            retryable_publish = fail_reason in {"internal", "video_pull_failed", "photo_pull_failed"}
            return PublishResult(
                self.platform,
                target.destination,
                False,
                f"TikTok publish failed: {fail_reason}",
                provider_tracking_id=tracking_id,
                raw_response=raw,
                error_code=f"tiktok_{fail_reason}",
                retryable=retryable_publish,
                status="failed",
            )
        return PublishResult(
            self.platform,
            target.destination,
            False,
            f"TikTok returned unknown publish status: {status or 'empty'}",
            provider_tracking_id=tracking_id,
            raw_response=raw,
            error_code="status_unknown",
            retryable=True,
            status="status_check_failed",
        )

    def _query_creator_info(self, requests, access_token: str) -> dict:
        try:
            response = requests.post(
                self.CREATOR_INFO_URL,
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=UTF-8"},
                json={},
                timeout=120,
            )
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(f"TikTok creator_info/query failed: {exc}") from exc
        data = response.json()
        if data.get("error", {}).get("code") not in {None, "", "ok"}:
            raise RuntimeError(f"TikTok creator_info/query failed: {data}")
        creator_info = data.get("data") or {}
        print(
            "[tiktok] creator info: "
            f"privacy={creator_info.get('privacy_level_options')} "
            f"comment_disabled={creator_info.get('comment_disabled')} "
            f"duet_disabled={creator_info.get('duet_disabled')} "
            f"stitch_disabled={creator_info.get('stitch_disabled')}"
        )
        return creator_info

    def _resolve_privacy_level(self, requested_privacy_level: str, creator_info: dict) -> str:
        options = creator_info.get("privacy_level_options") or []
        if not options:
            return requested_privacy_level
        if requested_privacy_level in options:
            return requested_privacy_level
        if "SELF_ONLY" in options:
            return "SELF_ONLY"
        return options[0]

    def _resolve_interaction_flag(self, requested_disabled: bool, account_forced_disabled: bool | None) -> str:
        effective_disabled = bool(requested_disabled) or bool(account_forced_disabled)
        return str(effective_disabled).lower()

    def _normalize_media(self, media_item: MediaItem) -> MediaItem | None:
        if media_item.media_type == "video":
            return media_item
        if media_item.is_remote and media_item.source.lower().endswith((".mp4", ".mov", ".mkv")):
            return MediaItem(media_item.source, "video", media_item.order_index, media_item.options)
        path = Path(media_item.source)
        if path.exists() and path.suffix.lower() in {".mp4", ".mov", ".mkv"}:
            return MediaItem(media_item.source, "video", media_item.order_index, media_item.options)
        return None

    def _publish_from_url(
        self,
        requests,
        access_token: str,
        video_url: str,
        title: str,
        post_mode: str,
        privacy_level: str,
        disable_comment: str,
        disable_duet: str,
        disable_stitch: str,
        progress_callback: Callable[[PublishResult], None] | None = None,
        destination: str | None = None,
    ) -> str:
        response = self._post_init(
            requests=requests,
            access_token=access_token,
            payload={
                "post_info": {
                    "title": title[:2200],
                    "privacy_level": privacy_level,
                    "disable_comment": disable_comment == "true",
                    "disable_duet": disable_duet == "true",
                    "disable_stitch": disable_stitch == "true",
                    "post_mode": post_mode,
                },
                "source_info": {
                    "source": "PULL_FROM_URL",
                    "video_url": video_url,
                },
            },
        )
        data = response.json()
        publish_id = data.get("data", {}).get("publish_id")
        if not publish_id:
            raise RuntimeError(f"TikTok init failed: {data}")
        if progress_callback is not None:
            progress_callback(PublishResult(
                self.platform,
                destination,
                True,
                f"TikTok publish accepted for processing: {publish_id}",
                provider_tracking_id=str(publish_id),
                raw_response={"publish_id": str(publish_id), "phase": "init_accepted"},
                status="processing",
            ))
        return str(publish_id)

    def _upload_local_file(
        self,
        requests,
        access_token: str,
        video_path: Path,
        title: str,
        post_mode: str,
        privacy_level: str,
        disable_comment: str,
        disable_duet: str,
        disable_stitch: str,
        progress_callback: Callable[[PublishResult], None] | None = None,
        destination: str | None = None,
    ) -> str:
        file_size = video_path.stat().st_size
        init_response = self._post_init(
            requests=requests,
            access_token=access_token,
            payload={
                "post_info": {
                    "title": title[:2200],
                    "privacy_level": privacy_level,
                    "disable_comment": disable_comment == "true",
                    "disable_duet": disable_duet == "true",
                    "disable_stitch": disable_stitch == "true",
                    "post_mode": post_mode,
                },
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": file_size,
                    "chunk_size": file_size,
                    "total_chunk_count": 1,
                },
            },
        )
        init_data = init_response.json()
        upload_url = init_data.get("data", {}).get("upload_url")
        publish_id = init_data.get("data", {}).get("publish_id")
        if not upload_url or not publish_id:
            raise RuntimeError(f"TikTok upload init failed: {init_data}")
        if progress_callback is not None:
            progress_callback(PublishResult(
                self.platform,
                destination,
                True,
                f"TikTok upload init accepted: {publish_id}",
                provider_tracking_id=str(publish_id),
                raw_response={"publish_id": str(publish_id), "phase": "init_accepted"},
                status="processing",
            ))
        upload_host = urlparse(upload_url).netloc
        print(f"[tiktok] upload host: {upload_host}")

        upload_response = self._upload_binary(
            requests=requests,
            upload_url=upload_url,
            video_path=video_path,
            file_size=file_size,
        )
        if upload_response.status_code not in {200, 201, 204}:
            raise RuntimeError(f"TikTok upload failed: {upload_response.status_code} {upload_response.text}")
        return publish_id

    # Do not automatically create a second TikTok publish after an ambiguous
    # FILE_UPLOAD failure. Once init returned a publish_id the provider may have
    # accepted the first publication, so fallback-by-republishing can duplicate it.

    def _post_init(self, requests, access_token: str, payload: dict):
        try:
            return requests.post(
                self.INIT_URL,
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
                json=payload,
                timeout=180,
            )
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(f"TikTok init request failed: {exc}") from exc

    def _upload_binary(self, requests, upload_url: str, video_path: Path, file_size: int):
        chunk_size = self._resolve_chunk_size(file_size)
        last_error = None
        for attempt in range(1, 4):
            try:
                return self._upload_chunks(
                    requests=requests,
                    upload_url=upload_url,
                    video_path=video_path,
                    file_size=file_size,
                    chunk_size=chunk_size,
                )
            except requests.exceptions.RequestException as exc:
                last_error = exc
                if attempt == 3:
                    break
                time.sleep(2 * attempt)
        raise RuntimeError(f"TikTok binary upload failed after retries: {last_error}") from last_error

    def _upload_chunks(self, requests, upload_url: str, video_path: Path, file_size: int, chunk_size: int):
        total_chunks = max(1, ceil(file_size / chunk_size))
        last_response = None
        with video_path.open("rb") as video_stream:
            for chunk_index in range(total_chunks):
                start_byte = chunk_index * chunk_size
                remaining = file_size - start_byte
                current_chunk_size = min(chunk_size, remaining)
                payload = video_stream.read(current_chunk_size)
                if len(payload) != current_chunk_size:
                    raise RuntimeError(
                        f"TikTok upload read mismatch: expected {current_chunk_size} bytes, got {len(payload)}"
                    )
                end_byte = start_byte + current_chunk_size - 1
                response = requests.put(
                    upload_url,
                    headers={
                        "Content-Type": self._content_type_for(video_path),
                        "Content-Length": str(current_chunk_size),
                        "Content-Range": f"bytes {start_byte}-{end_byte}/{file_size}",
                    },
                    data=payload,
                    timeout=1800,
                )
                last_response = response
                expected_codes = {206, 201} if chunk_index < total_chunks - 1 else {200, 201, 204}
                if response.status_code not in expected_codes:
                    raise RuntimeError(
                        f"TikTok upload chunk failed at {chunk_index + 1}/{total_chunks}: "
                        f"{response.status_code} {response.text}"
                    )
        return last_response

    def _resolve_chunk_size(self, file_size: int) -> int:
        if file_size <= 5 * 1024 * 1024:
            return file_size
        return min(64 * 1024 * 1024, file_size)

    def _content_type_for(self, video_path: Path) -> str:
        suffix = video_path.suffix.lower()
        if suffix == ".mov":
            return "video/quicktime"
        if suffix == ".webm":
            return "video/webm"
        return "video/mp4"
