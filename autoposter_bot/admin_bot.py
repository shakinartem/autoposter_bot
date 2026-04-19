from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests
from requests import exceptions as request_exceptions

from autoposter_bot.cloudinary_client import CloudinaryClient
from autoposter_bot.config import Settings, load_settings
from autoposter_bot.db import Database
from autoposter_bot.env_store import set_env_values
from autoposter_bot.models import MediaItem, PostJob, Target
from autoposter_bot.scheduler import process_due_db_jobs
from autoposter_bot.service import AutoposterService
from autoposter_bot.token_health import (
    build_instagram_token_warning_message,
    build_vk_token_warning_message,
)


@dataclass(slots=True)
class PostDraft:
    platform: str | None = None
    account_id: int | None = None
    content_kind: str | None = None
    text: str = ""
    media_items: list[MediaItem] = field(default_factory=list)


class TelegramAdminBot:
    def __init__(self, settings: Settings) -> None:
        if not settings.telegram_bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required for admin bot mode")
        self.settings = settings
        self.db = Database(settings.database_path)
        self.service = AutoposterService(settings)
        self.cloudinary = CloudinaryClient(settings)
        self.http = requests.Session()
        self.base_url = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
        self.file_base_url = f"https://api.telegram.org/file/bot{settings.telegram_bot_token}"
        self.upload_dir = settings.database_path.parent / "telegram_uploads"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.last_vk_warning_key: str | None = None
        self.last_instagram_warning_key: str | None = None
        self.last_warning_check_at = 0.0
        self.last_due_jobs_check_at = 0.0
        self.sessions: dict[int, dict[str, Any]] = {}

    def run(self) -> None:
        self.db.init_schema()
        offset = 0
        while True:
            self._maybe_send_token_warnings()
            self._maybe_process_due_jobs()
            try:
                updates = self._get_updates(offset)
            except request_exceptions.ReadTimeout:
                continue
            except request_exceptions.RequestException:
                time.sleep(3)
                continue
            for update in updates:
                offset = max(offset, int(update["update_id"]) + 1)
                self._handle_update(update)
            time.sleep(1)

    def _get_updates(self, offset: int) -> list[dict]:
        response = self.http.get(
            f"{self.base_url}/getUpdates",
            params={"offset": offset, "timeout": 25},
            timeout=(10, 35),
        )
        payload = response.json()
        if not payload.get("ok"):
            return []
        return payload.get("result", [])

    def _handle_update(self, update: dict) -> None:
        if update.get("callback_query"):
            self._handle_callback_query(update["callback_query"])
            return

        message = update.get("message") or update.get("edited_message")
        if not message:
            return
        chat_id = int(message["chat"]["id"])
        user_id = message.get("from", {}).get("id")
        text = (message.get("text") or "").strip()
        print(f"[admin-bot] update from user_id={user_id} chat_id={chat_id} text={text!r}")

        if text.startswith("/whoami"):
            reply = f"Ваш Telegram user id: {user_id}"
            print(f"[admin-bot] reply for chat_id={chat_id}: {reply}")
            self._safe_send_message(chat_id, reply)
            return
        if not self._is_allowed(user_id):
            reply = "Доступ запрещён. Добавьте ваш Telegram user id в TELEGRAM_ADMIN_USER_IDS."
            print(f"[admin-bot] reply for chat_id={chat_id}: {reply}")
            self._safe_send_message(chat_id, reply)
            return

        received_at = datetime.fromtimestamp(
            message.get("date", int(time.time())),
            tz=timezone.utc,
        ).astimezone().replace(tzinfo=None)

        if text.startswith("/"):
            try:
                reply = self._dispatch_command(text, received_at, chat_id)
            except Exception as exc:
                reply = f"Ошибка: {exc}"
            print(f"[admin-bot] reply for chat_id={chat_id}: {reply}")
            if reply:
                self._safe_send_message(chat_id, reply)
            return

        try:
            reply = self._handle_stateful_message(chat_id, message, received_at)
        except Exception as exc:
            reply = f"Ошибка: {exc}"
        print(f"[admin-bot] reply for chat_id={chat_id}: {reply}")
        if reply:
            self._safe_send_message(chat_id, reply)

    def _handle_callback_query(self, callback_query: dict) -> None:
        callback_id = callback_query["id"]
        message = callback_query.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = int(chat.get("id", 0))
        user_id = callback_query.get("from", {}).get("id")
        data = callback_query.get("data", "")
        print(f"[admin-bot] callback from user_id={user_id} chat_id={chat_id} data={data!r}")

        self._answer_callback_query(callback_id)
        if not self._is_allowed(user_id):
            reply = "Доступ запрещён."
        else:
            try:
                reply = self._dispatch_callback(chat_id, data)
            except Exception as exc:
                reply = f"Ошибка: {exc}"
        print(f"[admin-bot] reply for chat_id={chat_id}: {reply}")
        if reply:
            self._safe_send_message(chat_id, reply)

    def _dispatch_command(self, text: str, received_at: datetime, chat_id: int) -> str | None:
        if text.startswith("/start") or text.startswith("/menu") or text.startswith("/help"):
            self._reset_session(chat_id)
            self._send_main_menu(chat_id)
            return None
        if text.startswith("/accounts"):
            self._send_accounts_menu(chat_id)
            return self._accounts_text()
        if text.startswith("/vk_token_status"):
            return self._vk_token_status()
        if text.startswith("/set_vk_token "):
            return self._set_vk_token(text, received_at)
        if text.startswith("/instagram_token_status"):
            return self._instagram_token_status()
        if text.startswith("/set_instagram_token "):
            return self._set_instagram_token(text, received_at)
        if text.startswith("/delete_account "):
            return self._delete_account_command(text)
        if text.startswith("/add_telegram "):
            return self._add_telegram_command(text)
        if text.startswith("/add_vk "):
            return self._add_vk_command(text)
        if text.startswith("/add_instagram "):
            return self._add_instagram_command(text)
        if text.startswith("/add_tiktok "):
            return self._add_tiktok_command(text)
        if text.startswith("/test_text "):
            return self._test_text_command(text)
        if text.startswith("/test_photo "):
            return self._test_media_command(text, media_type="image")
        if text.startswith("/test_video "):
            return self._test_media_command(text, media_type="video")
        self._send_main_menu(chat_id)
        return "Неизвестная команда. Открыл главное меню."

    def _handle_stateful_message(self, chat_id: int, message: dict, received_at: datetime) -> str:
        session = self.sessions.get(chat_id)
        if not session:
            self._send_main_menu(chat_id)
            return "Используйте кнопки меню ниже."

        flow = session.get("flow")
        if flow == "account_add":
            return self._handle_add_account_message(chat_id, message)
        if flow == "account_edit":
            return self._handle_edit_account_message(chat_id, message, received_at)
        if flow == "post":
            return self._handle_post_message(chat_id, message, received_at)

        self._send_main_menu(chat_id)
        return "Используйте кнопки меню."

    def _dispatch_callback(self, chat_id: int, data: str) -> str | None:
        parts = data.split("|")
        if parts[:2] == ["menu", "main"]:
            self._reset_session(chat_id)
            self._send_main_menu(chat_id)
            return None
        if parts[:2] == ["menu", "accounts"]:
            self._reset_session(chat_id)
            self._send_accounts_menu(chat_id)
            return None
        if parts[:2] == ["menu", "post"]:
            self._start_post_flow(chat_id)
            return None
        if parts[:2] == ["menu", "run_due"]:
            processed = process_due_db_jobs(self.service, self.db, dry_run=False)
            return f"Обработано отложенных постов: {len(processed)}"

        if parts[0] == "acct":
            return self._dispatch_account_callback(chat_id, parts)
        if parts[0] == "post":
            return self._dispatch_post_callback(chat_id, parts)
        return "Неизвестное действие."

    def _dispatch_account_callback(self, chat_id: int, parts: list[str]) -> str:
        action = parts[1] if len(parts) > 1 else ""
        if action == "list":
            self._send_account_picker(chat_id, "acct|show", "Выберите аккаунт.")
            return None
        if action == "show":
            account_id = int(parts[2])
            row = self.db.get_account(account_id)
            if not row:
                return f"Аккаунт #{account_id} не найден."
            self._send_account_edit_menu(chat_id, row)
            return None
        if action == "add":
            if len(parts) == 2:
                self._send_platform_choice_menu(chat_id, prefix="acct|add")
                return None
            platform = parts[2]
            self.sessions[chat_id] = {"flow": "account_add", "platform": platform, "step": "name"}
            return f"Введите название аккаунта для {self._platform_label(platform)}."
        if action == "delete":
            if len(parts) == 2:
                self._send_account_picker(chat_id, "acct|delete", "Выберите аккаунт для удаления.")
                return None
            account_id = int(parts[2])
            row = self.db.get_account(account_id)
            if not row:
                return f"Аккаунт #{account_id} не найден."
            self.db.delete_account(account_id)
            self._send_accounts_menu(chat_id)
            return f"Удалён аккаунт #{account_id}: {row['platform']} | {row['name']} | {row['destination']}"
        if action == "edit":
            if len(parts) == 2:
                self._send_account_picker(chat_id, "acct|edit", "Выберите аккаунт для изменения.")
                return None
            if len(parts) == 3:
                account_id = int(parts[2])
                row = self.db.get_account(account_id)
                if not row:
                    return f"Аккаунт #{account_id} не найден."
                self._send_account_edit_menu(chat_id, row)
                return None
            account_id = int(parts[2])
            field_name = parts[3]
            row = self.db.get_account(account_id)
            if not row:
                return f"Аккаунт #{account_id} не найден."
            self.sessions[chat_id] = {
                "flow": "account_edit",
                "account_id": account_id,
                "field": field_name,
            }
            field_label = "новое название" if field_name == "name" else "новое значение destination"
            if field_name == "access_token":
                field_label = "новый access token"
            return f"Введите {field_label} для аккаунта #{account_id}."
        if action == "back":
            self._send_accounts_menu(chat_id)
            return None
        return "Неизвестное действие в меню аккаунтов."

    def _dispatch_post_callback(self, chat_id: int, parts: list[str]) -> str:
        action = parts[1] if len(parts) > 1 else ""
        session = self.sessions.setdefault(chat_id, {})
        draft: PostDraft = session.setdefault("draft", PostDraft())

        if action == "start":
            self._start_post_flow(chat_id)
            return "Выберите соцсеть."
        if action == "cancel":
            self._reset_session(chat_id)
            self._send_main_menu(chat_id)
            return None
        if action == "platform":
            platform = parts[2]
            draft.platform = platform
            draft.account_id = None
            draft.content_kind = None
            draft.text = ""
            draft.media_items.clear()
            accounts = self._accounts_for_platform(platform)
            if not accounts:
                return f"Для {self._platform_label(platform)} пока нет аккаунтов. Сначала добавьте аккаунт."
            if len(accounts) == 1:
                draft.account_id = int(accounts[0]["id"])
                self._send_content_menu(chat_id, platform, accounts[0])
                return None
            self.sessions[chat_id] = {"flow": "post", "draft": draft}
            self._send_platform_account_picker(chat_id, platform)
            return None
        if action == "account":
            account_id = int(parts[2])
            row = self.db.get_account(account_id)
            if not row:
                return f"Аккаунт #{account_id} не найден."
            draft.platform = row["platform"]
            draft.account_id = account_id
            draft.content_kind = None
            draft.text = ""
            draft.media_items.clear()
            self.sessions[chat_id] = {"flow": "post", "draft": draft}
            self._send_content_menu(chat_id, row["platform"], row)
            return None
        if action == "type":
            content_kind = parts[2]
            if not draft.platform or not draft.account_id:
                return "Сначала выберите соцсеть и аккаунт."
            if draft.platform == "instagram" and content_kind == "text":
                return "Для Instagram в текущем меню доступны фото и видео. Текст без медиа не публикуется."
            if draft.platform == "tiktok" and content_kind != "video":
                return "Для TikTok в текущей версии поддерживается только видео."
            draft.content_kind = content_kind
            draft.text = ""
            draft.media_items.clear()
            if content_kind == "text":
                self.sessions[chat_id] = {"flow": "post", "step": "await_text", "draft": draft}
                return "Отправьте текст поста одним сообщением."
            self.sessions[chat_id] = {"flow": "post", "step": "await_media", "draft": draft}
            self._send_media_collection_menu(chat_id, draft)
            return None
        if action == "add_text":
            if not draft.content_kind or draft.content_kind == "text":
                return "Сначала выберите фото или видео."
            self.sessions[chat_id] = {"flow": "post", "step": "await_caption", "draft": draft}
            return "Отправьте подпись к посту. Если подпись не нужна, нажмите «Готово»."
        if action == "done":
            return self._finalize_post_draft(chat_id, draft)
        if action == "send_now":
            result = self._publish_draft_now(draft)
            self._reset_session(chat_id)
            self._send_main_menu(chat_id)
            return result
        if action == "send_later":
            self.sessions[chat_id] = {"flow": "post", "step": "await_schedule", "draft": draft}
            return "Введите дату и время отложенного поста в формате YYYY-MM-DD HH:MM"
        if action == "back_socials":
            self._start_post_flow(chat_id)
            return None
        return "Неизвестное действие в меню постов."

    def _handle_add_account_message(self, chat_id: int, message: dict) -> str:
        session = self.sessions[chat_id]
        text = (message.get("text") or "").strip()
        if not text:
            return "Введите текстом значение, которое я попросил."
        if session["step"] == "name":
            session["name"] = text
            session["step"] = "destination"
            return self._destination_prompt(session["platform"])

        account_id = self._create_account(
            name=session["name"],
            platform=session["platform"],
            destination=text,
        )
        self._reset_session(chat_id)
        self._send_accounts_menu(chat_id)
        return f"Добавлен аккаунт #{account_id} для {self._platform_label(session['platform'])}."

    def _handle_edit_account_message(self, chat_id: int, message: dict, received_at: datetime) -> str:
        session = self.sessions[chat_id]
        account_id = int(session["account_id"])
        row = self.db.get_account(account_id)
        if not row:
            self._reset_session(chat_id)
            return f"Аккаунт #{account_id} не найден."

        text = (message.get("text") or "").strip()
        if not text:
            return "Введите новое значение текстом."

        if session["field"] == "name":
            self.db.update_account(account_id, name=text)
            result = f"Название аккаунта #{account_id} обновлено."
        elif session["field"] == "access_token":
            result = self._update_account_token(account_id, row, text, received_at)
        else:
            self.db.update_account(account_id, destination=text)
            result = f"Destination аккаунта #{account_id} обновлён."
        self._reset_session(chat_id)
        self._send_account_edit_menu(chat_id, self.db.get_account(account_id))
        return result

    def _handle_post_message(self, chat_id: int, message: dict, received_at: datetime) -> str:
        session = self.sessions[chat_id]
        draft: PostDraft = session["draft"]
        step = session.get("step")
        if step == "await_text":
            text = (message.get("text") or "").strip()
            if not text:
                return "Отправьте текст поста одним сообщением."
            draft.text = text
            self.sessions[chat_id] = {"flow": "post", "step": "ready", "draft": draft}
            self._send_ready_menu(chat_id, draft)
            return None
        if step == "await_caption":
            draft.text = (message.get("text") or "").strip()
            self.sessions[chat_id] = {"flow": "post", "step": "await_media", "draft": draft}
            self._send_media_collection_menu(chat_id, draft)
            return None
        if step == "await_media":
            media_item = self._extract_media_from_message(message, draft)
            if not media_item:
                expected = "фото" if draft.content_kind == "photo" else "видео"
                return f"Сейчас ожидается {expected}. Отправьте медиа или нажмите «Готово»."
            draft.media_items.append(media_item)
            self.sessions[chat_id] = {"flow": "post", "step": "await_media", "draft": draft}
            self._send_media_collection_menu(chat_id, draft)
            return None
        if step == "await_schedule":
            scheduled_at = self._parse_schedule_datetime((message.get("text") or "").strip())
            return self._schedule_draft(chat_id, draft, scheduled_at, received_at)

        self._send_main_menu(chat_id)
        return "Состояние не распознано. Открыл главное меню."

    def _destination_prompt(self, platform: str) -> str:
        if platform == "telegram":
            return "Введите destination для Telegram, например @my_channel или chat_id."
        if platform == "vk":
            return "Введите owner_id для VK, например -123456789."
        if platform == "instagram":
            return "Введите username Instagram, например my_instagram."
        if platform == "tiktok":
            return "Введите username TikTok, например my_tiktok."
        return "Введите destination аккаунта."

    def _create_account(self, name: str, platform: str, destination: str) -> int:
        if platform in {"telegram", "vk"}:
            options: dict[str, Any] = {}
        elif platform == "instagram":
            if not self.settings.instagram_ig_user_id or not self.settings.instagram_access_token:
                raise ValueError("Для Instagram заполните INSTAGRAM_IG_USER_ID и INSTAGRAM_ACCESS_TOKEN в .env")
            api_flow = "instagram_login" if self.settings.instagram_access_token.startswith("IG") else "facebook_login"
            options = {
                "ig_user_id": self.settings.instagram_ig_user_id,
                "access_token": self.settings.instagram_access_token,
                "graph_api_version": self.settings.instagram_graph_api_version,
                "api_flow": api_flow,
            }
        elif platform == "tiktok":
            if not self.settings.tiktok_access_token:
                raise ValueError("Для TikTok заполните TIKTOK_ACCESS_TOKEN в .env")
            options = {
                "access_token": self.settings.tiktok_access_token,
                "privacy_level": self.settings.tiktok_default_privacy_level,
                "post_mode": self.settings.tiktok_default_post_mode,
                "disable_comment": self.settings.tiktok_default_disable_comment,
                "disable_duet": self.settings.tiktok_default_disable_duet,
                "disable_stitch": self.settings.tiktok_default_disable_stitch,
            }
        else:
            raise ValueError(f"Неизвестная платформа: {platform}")
        return self.db.add_account(name=name, platform=platform, destination=destination, options=options)

    def _extract_media_from_message(self, message: dict, draft: PostDraft) -> MediaItem | None:
        if draft.content_kind == "photo":
            photos = message.get("photo") or []
            if not photos:
                return None
            file_id = photos[-1]["file_id"]
            local_path = self._download_telegram_file(file_id, ".jpg")
            options = self._media_options_for_platform(draft.platform or "", str(local_path), "image", draft.account_id or 0)
            return MediaItem(source=str(local_path), media_type="image", order_index=len(draft.media_items), options=options)

        if draft.content_kind == "video":
            video = message.get("video")
            if not video:
                return None
            file_name = video.get("file_name") or "video.mp4"
            suffix = Path(file_name).suffix or ".mp4"
            file_id = video["file_id"]
            local_path = self._download_telegram_file(file_id, suffix)
            options = self._media_options_for_platform(draft.platform or "", str(local_path), "video", draft.account_id or 0)
            return MediaItem(source=str(local_path), media_type="video", order_index=len(draft.media_items), options=options)

        return None

    def _media_options_for_platform(self, platform: str, source: str, media_type: str, account_id: int) -> dict[str, Any]:
        options: dict[str, Any] = {}
        if platform == "instagram":
            options["public_url"] = self.cloudinary.upload_media(
                source=source,
                media_type=media_type,
                public_id_prefix=f"instagram-bot-{account_id}",
            )
        return options

    def _download_telegram_file(self, file_id: str, suffix: str) -> Path:
        file_response = self.http.get(
            f"{self.base_url}/getFile",
            params={"file_id": file_id},
            timeout=(10, 60),
        ).json()
        file_path = file_response.get("result", {}).get("file_path")
        if not file_path:
            raise RuntimeError(f"Telegram getFile failed: {file_response}")
        content = self.http.get(f"{self.file_base_url}/{file_path}", timeout=(15, 300))
        content.raise_for_status()
        local_path = self.upload_dir / f"{int(time.time())}-{uuid4().hex}{suffix}"
        local_path.write_bytes(content.content)
        return local_path

    def _finalize_post_draft(self, chat_id: int, draft: PostDraft) -> str:
        if draft.content_kind in {"photo", "video"} and not draft.media_items:
            return "Сначала добавьте хотя бы одно медиа."
        if draft.platform in {"instagram", "tiktok"} and draft.content_kind == "text":
            return "Для выбранной платформы текст без медиа сейчас не поддерживается."
        self.sessions[chat_id] = {"flow": "post", "step": "ready", "draft": draft}
        self._send_ready_menu(chat_id, draft)
        return None

    def _publish_draft_now(self, draft: PostDraft) -> str:
        job = self._draft_to_job(draft)
        return self._format_results(self.service.publish_job(job, dry_run=False))

    def _schedule_draft(self, chat_id: int, draft: PostDraft, scheduled_at: datetime, received_at: datetime) -> str:
        job = self._draft_to_job(draft, scheduled_at=scheduled_at)
        account_ids = [target.account_id for target in job.targets if target.account_id is not None]
        job_id = self.db.create_job(
            post_id=job.post_id,
            content_type=job.content_type,
            text=job.text,
            scheduled_at=scheduled_at,
            media_items=job.media_items,
            account_ids=[int(account_id) for account_id in account_ids],
            metadata={"created_via": "admin_bot", "requested_at": received_at.isoformat()},
        )
        self._reset_session(chat_id)
        self._send_main_menu(chat_id)
        return f"Отложенный пост сохранён как job #{job_id}. Время отправки: {scheduled_at.isoformat(sep=' ', timespec='minutes')}"

    def _draft_to_job(self, draft: PostDraft, scheduled_at: datetime | None = None) -> PostJob:
        if not draft.platform or not draft.account_id or not draft.content_kind:
            raise ValueError("Черновик поста заполнен не полностью.")
        row = self.db.get_account(draft.account_id)
        if not row:
            raise ValueError(f"Аккаунт #{draft.account_id} не найден.")
        target = Target(
            platform=row["platform"],
            destination=row["destination"],
            account_id=int(row["id"]),
            account_name=row["name"],
            options=json.loads(row["options_json"] or "{}"),
        )
        content_type = self._resolve_content_type(target.platform, draft.content_kind, draft.media_items)
        return PostJob(
            post_id=f"bot-{uuid4().hex}",
            content_type=content_type,
            text=draft.text,
            media_items=list(draft.media_items),
            scheduled_at=scheduled_at,
            targets=[target],
            metadata={"source": "telegram_admin_bot"},
        )

    def _resolve_content_type(self, platform: str, content_kind: str, media_items: list[MediaItem]) -> str:
        if platform in {"telegram", "vk"}:
            return "crosspost"
        if platform == "instagram":
            if content_kind == "text":
                raise ValueError("Instagram не поддерживает текст без медиа в этом режиме.")
            if not media_items:
                raise ValueError("Instagram требует медиа.")
            if content_kind == "photo":
                return "instagram_carousel" if len(media_items) > 1 else "instagram_feed_image"
            return "instagram_carousel" if len(media_items) > 1 else "instagram_video"
        if platform == "tiktok":
            if content_kind != "video":
                raise ValueError("TikTok поддерживает только видео.")
            if len(media_items) != 1:
                raise ValueError("TikTok требует ровно одно видео.")
            return "tiktok_video"
        raise ValueError(f"Неизвестная платформа: {platform}")

    def _send_main_menu(self, chat_id: int) -> None:
        self._safe_send_message(
            chat_id,
            "Главное меню",
            reply_markup=self._keyboard(
                [
                    [("Создать пост", "menu|post"), ("Аккаунты", "menu|accounts")],
                    [("Запустить отложенные сейчас", "menu|run_due")],
                ]
            ),
        )

    def _send_accounts_menu(self, chat_id: int) -> None:
        self._safe_send_message(
            chat_id,
            "Меню аккаунтов",
            reply_markup=self._keyboard(
                [
                    [("Список аккаунтов", "acct|list"), ("Добавить", "acct|add")],
                    [("Изменить", "acct|edit"), ("Удалить", "acct|delete")],
                    [("Назад", "menu|main")],
                ]
            ),
        )

    def _send_platform_choice_menu(self, chat_id: int, prefix: str) -> None:
        self._safe_send_message(
            chat_id,
            "Выберите соцсеть",
            reply_markup=self._keyboard(
                [
                    [("Telegram", f"{prefix}|telegram"), ("VK", f"{prefix}|vk")],
                    [("Instagram", f"{prefix}|instagram"), ("TikTok", f"{prefix}|tiktok")],
                    [("Назад", "menu|accounts")],
                ]
            ),
        )

    def _send_account_picker(self, chat_id: int, prefix: str, title: str) -> None:
        rows: list[list[tuple[str, str]]] = []
        for row in self.db.list_accounts():
            rows.append([(f"{row['id']}: {row['platform']} / {row['name']}"[:30], f"{prefix}|{row['id']}")])
        rows.append([("Назад", "menu|accounts")])
        self._safe_send_message(chat_id, title, reply_markup=self._keyboard(rows))

    def _send_account_edit_menu(self, chat_id: int, row) -> None:
        account_id = int(row["id"])
        text = self._account_details_text(row)
        buttons = [
            [("Изменить название", f"acct|edit|{account_id}|name")],
            [("Изменить destination", f"acct|edit|{account_id}|destination")],
        ]
        if self._account_supports_token_edit(row):
            buttons.append([("Обновить access token", f"acct|edit|{account_id}|access_token")])
        buttons.append([("Назад", "menu|accounts")])
        self._safe_send_message(chat_id, text, reply_markup=self._keyboard(buttons))

    def _start_post_flow(self, chat_id: int) -> None:
        self.sessions[chat_id] = {"flow": "post", "draft": PostDraft()}
        self._safe_send_message(
            chat_id,
            "Соцсети",
            reply_markup=self._keyboard(
                [
                    [("Telegram", "post|platform|telegram"), ("VK", "post|platform|vk")],
                    [("Instagram", "post|platform|instagram"), ("TikTok", "post|platform|tiktok")],
                    [("Назад", "menu|main")],
                ]
            ),
        )

    def _send_platform_account_picker(self, chat_id: int, platform: str) -> None:
        rows: list[list[tuple[str, str]]] = []
        for row in self._accounts_for_platform(platform):
            rows.append([(f"{row['id']}: {row['name']}"[:30], f"post|account|{row['id']}")])
        rows.append([("Назад", "post|back_socials")])
        self._safe_send_message(
            chat_id,
            f"Аккаунты {self._platform_label(platform)}",
            reply_markup=self._keyboard(rows),
        )

    def _send_content_menu(self, chat_id: int, platform: str, row) -> None:
        text = (
            f"{self._platform_label(platform)}\n"
            f"Аккаунт: {row['name']} ({row['destination']})\n"
            "Выберите, что отправить:"
        )
        self._safe_send_message(
            chat_id,
            text,
            reply_markup=self._keyboard(
                [
                    [("Текст", "post|type|text"), ("Фото", "post|type|photo"), ("Видео", "post|type|video")],
                    [("Назад", "post|back_socials")],
                ]
            ),
        )

    def _send_media_collection_menu(self, chat_id: int, draft: PostDraft) -> None:
        text = f"Собрано медиа: {len(draft.media_items)}\n"
        if draft.text:
            text += f"Подпись: {draft.text[:100]}"
        else:
            text += "Подписи пока нет."
        self._safe_send_message(
            chat_id,
            text,
            reply_markup=self._keyboard(
                [
                    [("Добавить текст", "post|add_text"), ("Готово", "post|done")],
                    [("Отмена", "post|cancel")],
                ]
            ),
        )

    def _send_ready_menu(self, chat_id: int, draft: PostDraft) -> None:
        self._safe_send_message(
            chat_id,
            self._draft_summary(draft),
            reply_markup=self._keyboard(
                [
                    [("Отправить сейчас", "post|send_now"), ("Отправить позже", "post|send_later")],
                    [("Отмена", "post|cancel")],
                ]
            ),
        )

    def _draft_summary(self, draft: PostDraft) -> str:
        row = self.db.get_account(draft.account_id or 0)
        account_name = row["name"] if row else f"#{draft.account_id}"
        return "\n".join(
            [
                "Черновик поста готов.",
                f"Соцсеть: {self._platform_label(draft.platform or '-')}",
                f"Аккаунт: {account_name}",
                f"Тип: {draft.content_kind or '-'}",
                f"Текст: {draft.text or '(без текста)'}",
                f"Медиа: {len(draft.media_items)}",
            ]
        )

    def _account_details_text(self, row) -> str:
        options = json.loads(row["options_json"] or "{}")
        token_preview = self._masked_token_for_account(row["platform"], options)
        obtained_at, expires_at = self._token_dates_for_account(row["platform"])
        lines = [
            f"Аккаунт #{row['id']}",
            f"Платформа: {row['platform']}",
            f"Название: {row['name']}",
            f"Destination: {row['destination']}",
        ]
        if token_preview:
            lines.append(f"Token: {token_preview}")
        if obtained_at:
            lines.append(f"Получен: {obtained_at}")
        if expires_at:
            lines.append(f"Истекает: {expires_at}")
        return "\n".join(lines)

    def _masked_token_for_account(self, platform: str, options: dict[str, Any]) -> str | None:
        token = None
        if platform == "vk":
            token = self.settings.vk_token
        elif platform == "instagram":
            token = options.get("access_token") or self.settings.instagram_access_token
        elif platform == "tiktok":
            token = options.get("access_token") or self.settings.tiktok_access_token
        if not token:
            return None
        if len(token) <= 10:
            return token
        return f"{token[:6]}...{token[-4:]}"

    def _account_supports_token_edit(self, row) -> bool:
        return row["platform"] in {"vk", "instagram", "tiktok"}

    def _token_dates_for_account(self, platform: str) -> tuple[str | None, str | None]:
        if platform == "vk":
            return self.settings.vk_token_obtained_at, self.settings.vk_token_expires_at
        if platform == "instagram":
            return self.settings.instagram_token_obtained_at, self.settings.instagram_token_expires_at
        if platform == "tiktok":
            return self.settings.tiktok_token_obtained_at, self.settings.tiktok_token_expires_at
        return None, None

    def _keyboard(self, rows: list[list[tuple[str, str]]]) -> dict[str, Any]:
        return {
            "inline_keyboard": [
                [{"text": text, "callback_data": callback_data} for text, callback_data in row]
                for row in rows
            ]
        }

    def _update_account_token(self, account_id: int, row, token: str, received_at: datetime) -> str:
        platform = row["platform"]
        if platform == "vk":
            expires_at = self._resolve_expires_at(
                explicit_value=None,
                received_at=received_at,
                lifetime_seconds=self.settings.vk_token_lifetime_seconds,
                provider_label="VK",
                lifetime_env_name="VK_TOKEN_LIFETIME_SECONDS",
            )
            set_env_values(
                self.settings.env_file_path,
                {
                    "VK_TOKEN": token,
                    "VK_TOKEN_OBTAINED_AT": received_at.replace(microsecond=0).isoformat(),
                    "VK_TOKEN_EXPIRES_AT": expires_at,
                },
            )
            self._reload_runtime()
            return f"VK token обновлён в .env. Истекает: {self.settings.vk_token_expires_at}"

        options = json.loads(row["options_json"] or "{}")
        options["access_token"] = token
        self.db.update_account(account_id, options=options)

        if platform == "instagram":
            expires_at = self._resolve_expires_at(
                explicit_value=None,
                received_at=received_at,
                lifetime_seconds=self.settings.instagram_token_lifetime_seconds,
                provider_label="Instagram",
                lifetime_env_name="INSTAGRAM_TOKEN_LIFETIME_SECONDS",
            )
            set_env_values(
                self.settings.env_file_path,
                {
                    "INSTAGRAM_ACCESS_TOKEN": token,
                    "INSTAGRAM_TOKEN_OBTAINED_AT": received_at.replace(microsecond=0).isoformat(),
                    "INSTAGRAM_TOKEN_EXPIRES_AT": expires_at,
                },
            )
        elif platform == "tiktok":
            expires_at = self._resolve_expires_at(
                explicit_value=None,
                received_at=received_at,
                lifetime_seconds=self.settings.tiktok_token_lifetime_seconds,
                provider_label="TikTok",
                lifetime_env_name="TIKTOK_TOKEN_LIFETIME_SECONDS",
            )
            set_env_values(
                self.settings.env_file_path,
                {
                "TIKTOK_ACCESS_TOKEN": token,
                "TIKTOK_TOKEN_OBTAINED_AT": received_at.replace(microsecond=0).isoformat(),
                "TIKTOK_TOKEN_EXPIRES_AT": expires_at,
                },
            )
        self._reload_runtime()
        if platform == "instagram":
            return f"Access token для аккаунта #{account_id} обновлён. Истекает: {self.settings.instagram_token_expires_at}"
        if platform == "tiktok":
            return f"Access token для аккаунта #{account_id} обновлён. Истекает: {self.settings.tiktok_token_expires_at}"
        return f"Access token для аккаунта #{account_id} обновлён."

    def _safe_send_message(self, chat_id: int | str, text: str, reply_markup: dict[str, Any] | None = None) -> None:
        payload = {"chat_id": str(chat_id), "text": text[:4000]}
        if reply_markup is not None:
            payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                self.http.post(
                    f"{self.base_url}/sendMessage",
                    data=payload,
                    timeout=(20, 180),
                )
                return
            except Exception as exc:
                last_error = exc
                time.sleep(attempt)
        print(f"[admin-bot] sendMessage failed for chat_id={chat_id}: {last_error}")

    def _answer_callback_query(self, callback_id: str) -> None:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                self.http.post(
                    f"{self.base_url}/answerCallbackQuery",
                    data={"callback_query_id": callback_id},
                    timeout=(15, 60),
                )
                return
            except Exception as exc:
                last_error = exc
                time.sleep(attempt)
        print(f"[admin-bot] answerCallbackQuery failed: {last_error}")

    def _is_allowed(self, user_id: int | None) -> bool:
        if user_id is None:
            return False
        if not self.settings.telegram_admin_user_ids:
            return True
        return user_id in self.settings.telegram_admin_user_ids

    def _accounts_for_platform(self, platform: str) -> list:
        return [row for row in self.db.list_accounts() if row["platform"] == platform]

    def _accounts_text(self) -> str:
        accounts = self.db.list_accounts()
        if not accounts:
            return "Аккаунтов пока нет."
        lines = ["Аккаунты в БД:"]
        for row in accounts:
            lines.append(f"[{row['id']}] {row['platform']} | {row['name']} | {row['destination']}")
        return "\n".join(lines)

    def _vk_token_status(self) -> str:
        message = build_vk_token_warning_message(self.settings, warning_hours=1)
        if message:
            return message
        if self.settings.vk_token_expires_at:
            return f"VK token is active. Expires at {self.settings.vk_token_expires_at}"
        return "VK token is set. Expiration date is not configured."

    def _instagram_token_status(self) -> str:
        message = build_instagram_token_warning_message(self.settings, warning_hours=24)
        if message:
            return message
        if self.settings.instagram_token_expires_at:
            return f"Instagram token is active. Expires at {self.settings.instagram_token_expires_at}"
        return "Instagram token is set. Expiration date is not configured."

    def _set_vk_token(self, text: str, received_at: datetime) -> str:
        parts = text.split(maxsplit=2)
        if len(parts) < 2:
            raise ValueError("Формат: /set_vk_token <token> [expires_at_iso]")
        _, token = parts[:2]
        expires_at = self._resolve_expires_at(
            explicit_value=parts[2] if len(parts) >= 3 else None,
            received_at=received_at,
            lifetime_seconds=self.settings.vk_token_lifetime_seconds,
            provider_label="VK",
            lifetime_env_name="VK_TOKEN_LIFETIME_SECONDS",
        )
        set_env_values(
            self.settings.env_file_path,
            {
                "VK_TOKEN": token,
                "VK_TOKEN_EXPIRES_AT": expires_at,
                "VK_TOKEN_OBTAINED_AT": received_at.replace(microsecond=0).isoformat(),
            },
        )
        self._reload_runtime()
        return (
            "VK token updated in .env.\n"
            f"Obtained at: {self.settings.vk_token_obtained_at}\n"
            f"Expires at: {self.settings.vk_token_expires_at}\n"
            "Recommendation: renew the token in the same browser and from the same network/IP used by the bot."
        )

    def _set_instagram_token(self, text: str, received_at: datetime) -> str:
        parts = text.split(maxsplit=2)
        if len(parts) < 2:
            raise ValueError("Формат: /set_instagram_token <token> [expires_at_iso]")
        _, token = parts[:2]
        api_flow = "instagram_login" if token.startswith("IG") else "facebook_login"
        expires_at = self._resolve_expires_at(
            explicit_value=parts[2] if len(parts) >= 3 else None,
            received_at=received_at,
            lifetime_seconds=self.settings.instagram_token_lifetime_seconds,
            provider_label="Instagram",
            lifetime_env_name="INSTAGRAM_TOKEN_LIFETIME_SECONDS",
        )
        set_env_values(
            self.settings.env_file_path,
            {
                "INSTAGRAM_ACCESS_TOKEN": token,
                "INSTAGRAM_TOKEN_EXPIRES_AT": expires_at,
                "INSTAGRAM_TOKEN_OBTAINED_AT": received_at.replace(microsecond=0).isoformat(),
            },
        )
        self._reload_runtime()
        updated_accounts = self.db.replace_account_options_for_platform(
            "instagram",
            lambda options: {
                **options,
                "access_token": self.settings.instagram_access_token or token,
                "ig_user_id": options.get("ig_user_id") or self.settings.instagram_ig_user_id,
                "graph_api_version": options.get("graph_api_version") or self.settings.instagram_graph_api_version,
                "api_flow": api_flow,
            },
        )
        return (
            "Instagram token updated in .env.\n"
            f"Obtained at: {self.settings.instagram_token_obtained_at}\n"
            f"Expires at: {self.settings.instagram_token_expires_at}\n"
            f"Updated Instagram accounts in DB: {updated_accounts}\n"
            "Use OAuth link from /instagram_token_status when you need to renew it again."
        )

    def _delete_account_command(self, text: str) -> str:
        parts = text.split(maxsplit=1)
        if len(parts) != 2:
            raise ValueError("Формат: /delete_account <account_id>")
        account_id = int(parts[1])
        row = self.db.get_account(account_id)
        if not row:
            return f"Аккаунт #{account_id} не найден."
        self.db.delete_account(account_id)
        return f"Удалён аккаунт #{account_id}: {row['platform']} | {row['name']} | {row['destination']}"

    def _add_telegram_command(self, text: str) -> str:
        _, name, destination = text.split(maxsplit=2)
        account_id = self._create_account(name, "telegram", destination)
        return f"Добавлен Telegram-аккаунт #{account_id}: {name} -> {destination}"

    def _add_vk_command(self, text: str) -> str:
        _, name, destination = text.split(maxsplit=2)
        account_id = self._create_account(name, "vk", destination)
        return f"Добавлен VK-аккаунт #{account_id}: {name} -> {destination}"

    def _add_instagram_command(self, text: str) -> str:
        _, name, username = text.split(maxsplit=2)
        account_id = self._create_account(name, "instagram", username)
        return f"Добавлен Instagram-аккаунт #{account_id}: {name} -> {username}"

    def _add_tiktok_command(self, text: str) -> str:
        _, name, username = text.split(maxsplit=2)
        account_id = self._create_account(name, "tiktok", username)
        return f"Добавлен TikTok-аккаунт #{account_id}: {name} -> {username}"

    def _test_text_command(self, text: str) -> str:
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            raise ValueError("Формат: /test_text <account_id> <text>")
        _, account_id_text, body = parts
        account_id = int(account_id_text)
        job = self._build_test_job(account_id=account_id, text=body, media_items=[])
        return self._format_results(self.service.publish_job(job, dry_run=False))

    def _test_media_command(self, text: str, media_type: str) -> str:
        parts = text.split(maxsplit=3)
        if len(parts) < 3:
            raise ValueError("Формат: /test_photo <account_id> <path_or_url> [caption]")
        _, account_id_text, source = parts[:3]
        caption = parts[3] if len(parts) >= 4 else ""
        account_id = int(account_id_text)
        row = self.db.get_account(account_id)
        if not row:
            raise ValueError(f"Аккаунт #{account_id} не найден")
        media_options: dict[str, Any] = {}
        if source.startswith("http://") or source.startswith("https://"):
            media_options["public_url"] = source
        elif row["platform"] == "instagram":
            media_options["public_url"] = self.cloudinary.upload_media(
                source=source,
                media_type=media_type,
                public_id_prefix=f"instagram-test-{account_id}",
            )
        job = self._build_test_job(
            account_id=account_id,
            text=caption,
            media_items=[MediaItem(source=source, media_type=media_type, options=media_options)],
        )
        return self._format_results(self.service.publish_job(job, dry_run=False))

    def _build_test_job(self, account_id: int, text: str, media_items: list[MediaItem]) -> PostJob:
        row = self.db.get_account(account_id)
        if not row:
            raise ValueError(f"Аккаунт #{account_id} не найден")
        target = Target(
            platform=row["platform"],
            destination=row["destination"],
            account_id=int(row["id"]),
            account_name=row["name"],
            options=json.loads(row["options_json"] or "{}"),
        )
        content_kind = "text" if not media_items else media_items[0].media_type
        return PostJob(
            post_id=f"test-{account_id}-{int(time.time())}",
            content_type=self._resolve_content_type(target.platform, content_kind, media_items),
            text=text,
            media_items=media_items,
            targets=[target],
        )

    def _format_results(self, results: list) -> str:
        return "\n".join(
            f"[{'OK' if result.ok else 'FAIL'}] {result.platform} -> {result.destination}: {result.detail}"
            for result in results
        )

    def _maybe_send_token_warnings(self) -> None:
        now = time.time()
        if now - self.last_warning_check_at < 60:
            return
        self.last_warning_check_at = now
        warning_chat_id = self.settings.token_warning_chat_id
        if not warning_chat_id:
            return
        self.last_vk_warning_key = self._process_warning(
            warning_chat_id=warning_chat_id,
            message=build_vk_token_warning_message(self.settings, warning_hours=1),
            previous_key=self.last_vk_warning_key,
            expires_at=self.settings.vk_token_expires_at,
        )
        self.last_instagram_warning_key = self._process_warning(
            warning_chat_id=warning_chat_id,
            message=build_instagram_token_warning_message(self.settings, warning_hours=24),
            previous_key=self.last_instagram_warning_key,
            expires_at=self.settings.instagram_token_expires_at,
        )

    def _maybe_process_due_jobs(self) -> None:
        now = time.time()
        if now - self.last_due_jobs_check_at < 30:
            return
        self.last_due_jobs_check_at = now
        processed = process_due_db_jobs(self.service, self.db, dry_run=False)
        if not processed:
            return
        notify_chat_id = self.settings.token_warning_chat_id
        if not notify_chat_id and self.settings.telegram_admin_user_ids:
            notify_chat_id = self.settings.telegram_admin_user_ids[0]
        if notify_chat_id:
            self._safe_send_message(notify_chat_id, f"Опубликованы отложенные посты: {', '.join(processed)}")

    def _process_warning(
        self,
        warning_chat_id: str | int,
        message: str | None,
        previous_key: str | None,
        expires_at: str | None,
    ) -> str | None:
        if not message:
            return None
        warning_key = f"{expires_at}|{message}"
        if previous_key == warning_key:
            return previous_key
        self._safe_send_message(warning_chat_id, message)
        return warning_key

    def _reload_runtime(self) -> None:
        self.settings = load_settings(str(self.settings.env_file_path))
        self.service = AutoposterService(self.settings)
        self.cloudinary = CloudinaryClient(self.settings)
        self.http = requests.Session()
        self.base_url = f"https://api.telegram.org/bot{self.settings.telegram_bot_token}"
        self.file_base_url = f"https://api.telegram.org/file/bot{self.settings.telegram_bot_token}"

    def _resolve_expires_at(
        self,
        explicit_value: str | None,
        received_at: datetime,
        lifetime_seconds: int | None,
        provider_label: str,
        lifetime_env_name: str,
    ) -> str:
        if explicit_value:
            datetime.fromisoformat(explicit_value)
            return explicit_value
        if not lifetime_seconds:
            raise ValueError(
                f"Для {provider_label} укажите expires_at или заполните {lifetime_env_name} в .env"
            )
        return (received_at + timedelta(seconds=lifetime_seconds)).replace(microsecond=0).isoformat()

    def _parse_schedule_datetime(self, raw_value: str) -> datetime:
        normalized = raw_value.strip().replace(" ", "T")
        if not normalized:
            raise ValueError("Введите дату и время в формате YYYY-MM-DD HH:MM")
        scheduled_at = datetime.fromisoformat(normalized)
        if scheduled_at <= datetime.now():
            raise ValueError("Дата отложенного поста должна быть в будущем.")
        return scheduled_at

    def _platform_label(self, platform: str) -> str:
        return {
            "telegram": "Telegram",
            "vk": "VK",
            "instagram": "Instagram",
            "tiktok": "TikTok",
        }.get(platform, platform)

    def _reset_session(self, chat_id: int) -> None:
        self.sessions.pop(chat_id, None)
