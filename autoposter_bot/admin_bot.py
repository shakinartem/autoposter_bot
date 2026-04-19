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

PLATFORM_ADMIN_IDS = {5027592714}
REFERRAL_REWARD_REFERRER = 100
REFERRAL_REWARD_REFERRED = 100


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
        self.chat_user_ids: dict[int, int] = {}

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
        self._ensure_current_user(chat_id, message.get("from") or {})
        start_ref_message = self._maybe_apply_referral(chat_id, text)

        received_at = datetime.fromtimestamp(
            message.get("date", int(time.time())),
            tz=timezone.utc,
        ).astimezone().replace(tzinfo=None)

        if text.startswith("/"):
            try:
                reply = self._dispatch_command(text, received_at, chat_id)
            except Exception as exc:
                reply = f"Ошибка: {exc}"
            if start_ref_message:
                reply = f"{start_ref_message}\n\n{reply}" if reply else start_ref_message
            print(f"[admin-bot] reply for chat_id={chat_id}: {reply}")
            if reply:
                self._safe_send_message(chat_id, reply)
            return

        try:
            reply = self._handle_stateful_message(chat_id, message, received_at)
        except Exception as exc:
            reply = f"Ошибка: {exc}"
        if start_ref_message:
            reply = f"{start_ref_message}\n\n{reply}" if reply else start_ref_message
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
            self._ensure_current_user(chat_id, callback_query.get("from") or {})
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
            return self._accounts_text(chat_id)
        if text.startswith("/vk_token_status"):
            return self._vk_token_status()
        if text.startswith("/set_vk_token "):
            return self._set_vk_token(text, received_at)
        if text.startswith("/instagram_token_status"):
            return self._instagram_token_status()
        if text.startswith("/set_instagram_token "):
            return self._set_instagram_token(text, received_at, chat_id)
        if text.startswith("/delete_account "):
            return self._delete_account_command(text, chat_id)
        if text.startswith("/add_telegram "):
            return self._add_telegram_command(text, chat_id)
        if text.startswith("/add_vk "):
            return self._add_vk_command(text, chat_id)
        if text.startswith("/add_instagram "):
            return self._add_instagram_command(text, chat_id)
        if text.startswith("/add_tiktok "):
            return self._add_tiktok_command(text, chat_id)
        if text.startswith("/test_text "):
            return self._test_text_command(text, chat_id)
        if text.startswith("/test_photo "):
            return self._test_media_command(text, media_type="image", chat_id=chat_id)
        if text.startswith("/test_video "):
            return self._test_media_command(text, media_type="video", chat_id=chat_id)
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
        if parts[:2] == ["menu", "profile"]:
            self._reset_session(chat_id)
            self._send_profile_menu(chat_id)
            return None
        if parts[:2] == ["menu", "plans"]:
            self._send_plans_menu(chat_id)
            return None
        if parts[:2] == ["menu", "billing"]:
            self._send_billing_menu(chat_id)
            return None
        if parts[:2] == ["menu", "addons"]:
            self._send_addons_menu(chat_id)
            return None
        if parts[:2] == ["menu", "partner"]:
            self._send_partner_menu(chat_id)
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
        if parts[0] == "bill":
            return self._dispatch_billing_callback(chat_id, parts)
        if parts[0] == "addons":
            return self._dispatch_addons_callback(chat_id, parts)
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
            row = self.db.get_account(account_id, owner_user_id=self._current_user_id(chat_id))
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
            row = self.db.get_account(account_id, owner_user_id=self._current_user_id(chat_id))
            if not row:
                return f"Аккаунт #{account_id} не найден."
            self.db.delete_account(account_id, owner_user_id=self._current_user_id(chat_id))
            self._send_accounts_menu(chat_id)
            return f"Удалён аккаунт #{account_id}: {row['platform']} | {row['name']} | {row['destination']}"
        if action == "edit":
            if len(parts) == 2:
                self._send_account_picker(chat_id, "acct|edit", "Выберите аккаунт для изменения.")
                return None
            if len(parts) == 3:
                account_id = int(parts[2])
                row = self.db.get_account(account_id, owner_user_id=self._current_user_id(chat_id))
                if not row:
                    return f"Аккаунт #{account_id} не найден."
                self._send_account_edit_menu(chat_id, row)
                return None
            account_id = int(parts[2])
            field_name = parts[3]
            row = self.db.get_account(account_id, owner_user_id=self._current_user_id(chat_id))
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

    def _dispatch_addons_callback(self, chat_id: int, parts: list[str]) -> str:
        if len(parts) < 3 or parts[1] != "buy":
            return "Неизвестное действие в меню допов."
        owner_user_id = self._current_user_id(chat_id)
        addon_kind = parts[2]
        if addon_kind == "post":
            detail = self._purchase_extra_post(owner_user_id)
            self._send_addons_menu(chat_id)
            return detail
        if addon_kind == "account":
            if len(parts) < 4:
                return "Выберите соцсеть для покупки доп. аккаунта."
            platform = parts[3]
            detail = self._purchase_extra_account(owner_user_id, platform)
            self._send_addons_menu(chat_id)
            return detail
        return "Неизвестный тип допа."

    def _dispatch_billing_callback(self, chat_id: int, parts: list[str]) -> str:
        if len(parts) < 2:
            return "Неизвестное действие в биллинге."
        action = parts[1]
        if action == "open":
            self._send_billing_menu(chat_id)
            return None
        if action == "plans":
            self._send_plan_switch_menu(chat_id)
            return None
        if action == "history":
            return self._credit_history_text(self._current_user_id(chat_id))
        if action == "switch" and len(parts) >= 3:
            return self._switch_plan(chat_id, parts[2])
        return "Неизвестное действие в биллинге."

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
            accounts = self._accounts_for_platform(platform, chat_id)
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
            row = self.db.get_account(account_id, owner_user_id=self._current_user_id(chat_id))
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
            result = self._publish_draft_now(draft, self._current_user_id(chat_id))
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
            owner_user_id=self._current_user_id(chat_id),
        )
        self._reset_session(chat_id)
        self._send_accounts_menu(chat_id)
        return f"Добавлен аккаунт #{account_id} для {self._platform_label(session['platform'])}."

    def _handle_edit_account_message(self, chat_id: int, message: dict, received_at: datetime) -> str:
        session = self.sessions[chat_id]
        account_id = int(session["account_id"])
        owner_user_id = self._current_user_id(chat_id)
        row = self.db.get_account(account_id, owner_user_id=owner_user_id)
        if not row:
            self._reset_session(chat_id)
            return f"Аккаунт #{account_id} не найден."

        text = (message.get("text") or "").strip()
        if not text:
            return "Введите новое значение текстом."

        if session["field"] == "name":
            self.db.update_account(account_id, name=text, owner_user_id=owner_user_id)
            result = f"Название аккаунта #{account_id} обновлено."
        elif session["field"] == "access_token":
            result = self._update_account_token(account_id, row, text, received_at, chat_id)
        else:
            self.db.update_account(account_id, destination=text, owner_user_id=owner_user_id)
            result = f"Destination аккаунта #{account_id} обновлён."
        self._reset_session(chat_id)
        refreshed_row = self.db.get_account(account_id, owner_user_id=owner_user_id)
        if refreshed_row:
            self._send_account_edit_menu(chat_id, refreshed_row)
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

    def _create_account(self, name: str, platform: str, destination: str, owner_user_id: int) -> int:
        self._enforce_account_creation_pricing(owner_user_id, platform)
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
        return self.db.add_account(
            name=name,
            platform=platform,
            destination=destination,
            options=options,
            owner_user_id=owner_user_id,
        )

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

    def _publish_draft_now(self, draft: PostDraft, owner_user_id: int) -> str:
        job = self._draft_to_job(draft, owner_user_id=owner_user_id)
        results = self.service.publish_job(job, dry_run=False)
        if results and all(result.ok for result in results):
            charge_detail = self._charge_for_post_if_needed(owner_user_id)
            self.db.create_job(
                post_id=job.post_id,
                content_type=job.content_type,
                text=job.text,
                scheduled_at=None,
                media_items=job.media_items,
                account_ids=[int(target.account_id) for target in job.targets if target.account_id is not None],
                metadata={"created_via": "admin_bot", "published_immediately": True},
                owner_user_id=owner_user_id,
                status="published",
            )
            message = self._format_results(results)
            if charge_detail:
                message = f"{message}\n{charge_detail}"
            return message
        return self._format_results(results)

    def _schedule_draft(self, chat_id: int, draft: PostDraft, scheduled_at: datetime, received_at: datetime) -> str:
        owner_user_id = self._current_user_id(chat_id)
        charge_detail = self._charge_for_post_if_needed(owner_user_id)
        job = self._draft_to_job(draft, scheduled_at=scheduled_at, owner_user_id=owner_user_id)
        account_ids = [target.account_id for target in job.targets if target.account_id is not None]
        job_id = self.db.create_job(
            post_id=job.post_id,
            content_type=job.content_type,
            text=job.text,
            scheduled_at=scheduled_at,
            media_items=job.media_items,
            account_ids=[int(account_id) for account_id in account_ids],
            metadata={"created_via": "admin_bot", "requested_at": received_at.isoformat()},
            owner_user_id=owner_user_id,
        )
        self._reset_session(chat_id)
        self._send_main_menu(chat_id)
        message = f"Отложенный пост сохранён как job #{job_id}. Время отправки: {scheduled_at.isoformat(sep=' ', timespec='minutes')}"
        if charge_detail:
            message = f"{message}\n{charge_detail}"
        return message

    def _draft_to_job(
        self,
        draft: PostDraft,
        scheduled_at: datetime | None = None,
        owner_user_id: int | None = None,
    ) -> PostJob:
        if not draft.platform or not draft.account_id or not draft.content_kind:
            raise ValueError("Черновик поста заполнен не полностью.")
        row = self.db.get_account(draft.account_id, owner_user_id=owner_user_id)
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
            "✨ Главное меню",
            reply_markup=self._keyboard(
                [
                    [("📝 Создать пост", "menu|post"), ("🔗 Аккаунты", "menu|accounts")],
                    [("👤 Профиль", "menu|profile"), ("💼 Тарифы", "menu|plans")],
                    [("💳 Биллинг", "menu|billing"), ("🧩 Допы", "menu|addons")],
                    [("🤝 Партнёрка", "menu|partner")],
                    [("⏰ Запустить отложенные", "menu|run_due")],
                ]
            ),
        )

    def _send_profile_menu(self, chat_id: int) -> None:
        self._safe_send_message(
            chat_id,
            self._profile_text(chat_id),
            reply_markup=self._keyboard(
                [
                    [("💼 Тарифы", "menu|plans"), ("🔗 Аккаунты", "menu|accounts")],
                    [("💳 Биллинг", "menu|billing"), ("🧩 Допы", "menu|addons")],
                    [("🤝 Партнёрка", "menu|partner")],
                    [("📝 Создать пост", "menu|post")],
                    [("⬅️ Назад", "menu|main")],
                ]
            ),
        )

    def _send_plans_menu(self, chat_id: int) -> None:
        self._safe_send_message(
            chat_id,
            self._plans_text(chat_id),
            reply_markup=self._keyboard(
                [
                    [("💳 Биллинг", "menu|billing"), ("🧩 Допы", "menu|addons")],
                    [("👤 Профиль", "menu|profile")],
                    [("⬅️ Назад", "menu|main")],
                ]
            ),
        )

    def _send_billing_menu(self, chat_id: int) -> None:
        self._safe_send_message(
            chat_id,
            self._billing_text(chat_id),
            reply_markup=self._keyboard(
                [
                    [("🔁 Сменить тариф", "bill|plans"), ("📜 История", "bill|history")],
                    [("🧩 Допы", "menu|addons"), ("👤 Профиль", "menu|profile")],
                    [("⬅️ Назад", "menu|main")],
                ]
            ),
        )

    def _send_plan_switch_menu(self, chat_id: int) -> None:
        rows: list[list[tuple[str, str]]] = []
        current = self.db.get_active_subscription(self._current_user_id(chat_id))
        current_code = current["plan_code"] if current else None
        for plan in self.db.list_plans():
            label = f"{self._plan_emoji(plan['code'])} {plan['name']}"
            if plan["code"] == current_code:
                label = f"{label} ✅"
            rows.append([(label[:30], f"bill|switch|{plan['code']}")])
        rows.append([("💳 Назад в биллинг", "menu|billing")])
        self._safe_send_message(chat_id, "💳 Выберите тариф для переключения", reply_markup=self._keyboard(rows))

    def _send_addons_menu(self, chat_id: int) -> None:
        self._safe_send_message(
            chat_id,
            self._addons_text(chat_id),
            reply_markup=self._keyboard(
                [
                    [("📝 Купить +1 пост", "addons|buy|post")],
                    [("🔗 +1 Telegram", "addons|buy|account|telegram"), ("🔗 +1 VK", "addons|buy|account|vk")],
                    [("🔗 +1 Instagram", "addons|buy|account|instagram"), ("🔗 +1 TikTok", "addons|buy|account|tiktok")],
                    [("👤 Профиль", "menu|profile"), ("⬅️ Назад", "menu|main")],
                ]
            ),
        )

    def _send_partner_menu(self, chat_id: int) -> None:
        self._safe_send_message(
            chat_id,
            self._partner_text(chat_id),
            reply_markup=self._keyboard(
                [
                    [("👤 Профиль", "menu|profile"), ("⬅️ Назад", "menu|main")],
                ]
            ),
        )

    def _send_accounts_menu(self, chat_id: int) -> None:
        self._safe_send_message(
            chat_id,
            "Меню аккаунтов",
            reply_markup=self._keyboard(
                [
                    [("📚 Список аккаунтов", "acct|list"), ("➕ Добавить", "acct|add")],
                    [("✏️ Изменить", "acct|edit"), ("🗑️ Удалить", "acct|delete")],
                    [("⬅️ Назад", "menu|main")],
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
        for row in self.db.list_accounts(owner_user_id=self._current_user_id(chat_id)):
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
        for row in self._accounts_for_platform(platform, chat_id):
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
            self._draft_summary(draft, owner_user_id=self._current_user_id(chat_id)),
            reply_markup=self._keyboard(
                [
                    [("Отправить сейчас", "post|send_now"), ("Отправить позже", "post|send_later")],
                    [("Отмена", "post|cancel")],
                ]
            ),
        )

    def _draft_summary(self, draft: PostDraft, owner_user_id: int | None = None) -> str:
        row = self.db.get_account(draft.account_id or 0, owner_user_id=owner_user_id)
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

    def _update_account_token(self, account_id: int, row, token: str, received_at: datetime, chat_id: int) -> str:
        platform = row["platform"]
        owner_user_id = self._current_user_id(chat_id)
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
        self.db.update_account(account_id, options=options, owner_user_id=owner_user_id)

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

    def _ensure_current_user(self, chat_id: int, user_payload: dict[str, Any]) -> int:
        telegram_user_id = int(user_payload.get("id", 0))
        if telegram_user_id <= 0:
            raise ValueError("Не удалось определить Telegram user id.")
        existing_user = self.db.get_user_by_telegram_id(telegram_user_id)
        username = user_payload.get("username")
        first_name = (user_payload.get("first_name") or "").strip()
        last_name = (user_payload.get("last_name") or "").strip()
        full_name = " ".join(part for part in [first_name, last_name] if part) or username or f"user-{telegram_user_id}"
        row = self.db.ensure_user(telegram_user_id, username, full_name)
        owner_user_id = int(row["id"])
        if existing_user is None:
            self._bootstrap_new_user(owner_user_id)
        self._ensure_platform_role(telegram_user_id, owner_user_id)
        self.chat_user_ids[chat_id] = owner_user_id
        return owner_user_id

    def _current_user_id(self, chat_id: int) -> int:
        owner_user_id = self.chat_user_ids.get(chat_id)
        if owner_user_id is None:
            raise ValueError("Пользователь не инициализирован. Откройте /start ещё раз.")
        return owner_user_id

    def _bootstrap_new_user(self, user_id: int) -> None:
        subscription = self.db.get_active_subscription(user_id)
        if subscription is not None:
            return
        trial_plan = next((plan for plan in self.db.list_plans() if plan["code"] == "trial"), None)
        if trial_plan is None:
            return
        self.db.activate_subscription(user_id, int(trial_plan["id"]))
        grant = int(trial_plan["monthly_credit_grant"] or 0)
        if grant > 0:
            self.db.add_credit_transaction(
                user_id,
                grant,
                "trial_grant",
                {"plan_code": trial_plan["code"]},
            )

    def _ensure_platform_role(self, telegram_user_id: int, user_id: int) -> None:
        if telegram_user_id in PLATFORM_ADMIN_IDS:
            user = self.db.get_user(user_id)
            if user and user["role"] != "admin":
                self.db.set_user_role(user_id, "admin")

    def _maybe_apply_referral(self, chat_id: int, text: str) -> str | None:
        if not text.startswith("/start"):
            return None
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            return None
        payload = parts[1].strip()
        if not payload.startswith("ref_"):
            return None

        code = payload[4:].strip().lower()
        if not code:
            return None
        referred_user_id = self._current_user_id(chat_id)
        if self.db.has_referral_event_for_referred_user(referred_user_id):
            return "🤝 Реферальный код уже был применён ранее."

        owner = self.db.get_referral_code_owner(code)
        if owner is None:
            return "🤝 Реферальный код не найден или уже недоступен."

        referrer_user_id = int(owner["user_id"])
        if referrer_user_id == referred_user_id:
            return "🤝 Нельзя активировать собственный реферальный код."

        self.db.create_referral_event(
            referrer_user_id=referrer_user_id,
            referred_user_id=referred_user_id,
            code=code,
            status="registered",
            reward_amount=REFERRAL_REWARD_REFERRER + REFERRAL_REWARD_REFERRED,
            metadata={"source": "telegram_start_payload"},
        )
        self.db.add_credit_transaction(
            referrer_user_id,
            REFERRAL_REWARD_REFERRER,
            "referral_reward_referrer",
            {"code": code, "referred_user_id": referred_user_id},
        )
        self.db.add_credit_transaction(
            referred_user_id,
            REFERRAL_REWARD_REFERRED,
            "referral_reward_referred",
            {"code": code, "referrer_user_id": referrer_user_id},
        )
        referrer_name = owner["full_name"] or (f"@{owner['username']}" if owner["username"] else "партнёр")
        return (
            "🤝 Реферальный код активирован.\n"
            f"Вас пригласил: {referrer_name}\n"
            f"Вам начислено: {REFERRAL_REWARD_REFERRED} кредитов.\n"
            f"Партнёру начислено: {REFERRAL_REWARD_REFERRER} кредитов."
        )

    def _accounts_for_platform(self, platform: str, chat_id: int) -> list:
        return [
            row
            for row in self.db.list_accounts(owner_user_id=self._current_user_id(chat_id))
            if row["platform"] == platform
        ]

    def _accounts_text(self, chat_id: int) -> str:
        accounts = self.db.list_accounts(owner_user_id=self._current_user_id(chat_id))
        if not accounts:
            return "Аккаунтов пока нет."
        lines = ["Аккаунты в БД:"]
        for row in accounts:
            lines.append(f"[{row['id']}] {row['platform']} | {row['name']} | {row['destination']}")
        return "\n".join(lines)

    def _profile_text(self, chat_id: int) -> str:
        user_id = self._current_user_id(chat_id)
        user = self.db.get_user(user_id)
        if not user:
            return "👤 Профиль пока недоступен."
        subscription = self.db.get_active_subscription(user_id)
        accounts_count = len(self.db.list_accounts(owner_user_id=user_id))
        plan_name = subscription["plan_name"] if subscription else "Без подписки"
        plan_code = subscription["plan_code"] if subscription else "-"
        expires_at = subscription["expires_at"] if subscription and subscription["expires_at"] else "Не ограничено"
        username = f"@{user['username']}" if user["username"] else "не указан"
        plans_by_code = {plan["code"]: plan for plan in self.db.list_plans()}
        active_plan = plans_by_code.get(plan_code)
        plan_features = json.loads(active_plan["features_json"] or "{}") if active_plan else {}
        accounts_per_platform = plan_features.get("accounts_per_platform", "-")
        post_limit = active_plan["monthly_post_limit"] if active_plan and active_plan["monthly_post_limit"] is not None else "-"
        month_posts_used = self._monthly_post_usage(user_id)
        extra_posts_bought = self._extra_post_allowance(user_id)
        usage_lines = self._account_usage_lines(user_id, int(accounts_per_platform) if accounts_per_platform != "-" else 0)
        return "\n".join(
            [
                "👤 Профиль",
                f"Имя: {user['full_name']}",
                f"Username: {username}",
                f"Роль: {user['role']}",
                "",
                "💼 Текущий тариф",
                f"{plan_name} ({plan_code})",
                f"Лимит аккаунтов: {accounts_per_platform} на каждую соцсеть",
                f"Лимит постов: {post_limit} в месяц",
                f"Подписка до: {expires_at}",
                "",
                "📊 Использование",
                f"Постов в этом месяце: {month_posts_used} / {post_limit}",
                f"Куплено доп. постов: {extra_posts_bought}",
                *usage_lines,
                "",
                "💳 Баланс",
                f"Кредиты: {user['credit_balance']}",
                f"Подключено аккаунтов: {accounts_count}",
            ]
        )

    def _plans_text(self, chat_id: int) -> str:
        user_id = self._current_user_id(chat_id)
        active_subscription = self.db.get_active_subscription(user_id)
        active_code = active_subscription["plan_code"] if active_subscription else None
        lines = [
            "💼 Тарифы",
            "",
            "➕ Доп. аккаунт: 350 ₽",
            "➕ Доп. пост: 35 ₽",
            "",
        ]
        for plan in self.db.list_plans():
            features = json.loads(plan["features_json"] or "{}")
            marker = " ✅ текущий" if plan["code"] == active_code else ""
            accounts_per_platform = features.get("accounts_per_platform", "-")
            support = features.get("support", "-")
            post_limit = plan["monthly_post_limit"] if plan["monthly_post_limit"] is not None else "без лимита"
            extra_account_price = features.get("extra_account_price_rub", 350)
            extra_post_price = features.get("extra_post_price_rub", 35)
            lines.extend(
                [
                    f"{self._plan_emoji(plan['code'])} {plan['name']}{marker}",
                    f"💰 Цена: {plan['price_rub']} ₽/мес",
                    f"🔗 Аккаунты: {accounts_per_platform} на каждую соцсеть",
                    f"📝 Посты: {post_limit} в месяц",
                    f"🎟️ Доп. аккаунт: {extra_account_price} ₽",
                    f"🎟️ Доп. пост: {extra_post_price} ₽",
                    f"🤝 Поддержка: {support}",
                    "",
                ]
            )
        lines.append("Следующий шаг: подключим оплату, апгрейд тарифа и покупку допкредитов прямо из бота.")
        return "\n".join(lines)

    def _billing_text(self, chat_id: int) -> str:
        user_id = self._current_user_id(chat_id)
        user = self.db.get_user(user_id)
        if not user:
            return "💳 Биллинг пока недоступен."
        subscription = self.db.get_active_subscription(user_id)
        plan_name = subscription["plan_name"] if subscription else "Без тарифа"
        expires_at = subscription["expires_at"] if subscription and subscription["expires_at"] else "Не ограничено"
        history = self.db.list_credit_ledger(user_id, limit=5)
        lines = [
            "💳 Биллинг",
            f"Текущий тариф: {plan_name}",
            f"Подписка до: {expires_at}",
            f"Баланс кредитов: {user['credit_balance']}",
            "",
            "Последние операции:",
        ]
        if not history:
            lines.append("Пока без операций.")
        else:
            for row in history:
                lines.append(self._format_credit_ledger_row(row))
        lines.append("")
        lines.append("Смена тарифа сейчас работает в тестовом режиме без реальной оплаты.")
        return "\n".join(lines)

    def _credit_history_text(self, user_id: int) -> str:
        history = self.db.list_credit_ledger(user_id, limit=20)
        lines = ["📜 История кредитов"]
        if not history:
            lines.append("Пока записей нет.")
            return "\n".join(lines)
        lines.extend(self._format_credit_ledger_row(row) for row in history)
        return "\n".join(lines)

    def _format_credit_ledger_row(self, row) -> str:
        delta = int(row["delta"])
        sign = "+" if delta >= 0 else ""
        created_at = str(row["created_at"]).replace("T", " ")[:16]
        reason = str(row["reason"])
        return f"• {created_at} | {sign}{delta} | {reason}"

    def _plan_emoji(self, plan_code: str) -> str:
        return {
            "trial": "🌱",
            "start": "🚀",
            "growth": "📈",
            "business": "🏢",
            "scale": "👑",
        }.get(plan_code, "💼")

    def _switch_plan(self, chat_id: int, plan_code: str) -> str:
        user_id = self._current_user_id(chat_id)
        plan = self.db.get_plan_by_code(plan_code)
        if not plan or not int(plan["is_active"]):
            return "Тариф недоступен."
        current = self.db.get_active_subscription(user_id)
        if current and current["plan_code"] == plan_code:
            self._send_billing_menu(chat_id)
            return f"Тариф {plan['name']} уже активен."

        self.db.activate_subscription(user_id, int(plan["id"]))
        grant = int(plan["monthly_credit_grant"] or 0)
        if grant > 0:
            self.db.add_credit_transaction(
                user_id,
                grant,
                "plan_switch_grant",
                {"plan_code": plan["code"]},
            )
        self._send_billing_menu(chat_id)
        return (
            f"✅ Тариф переключён на {plan['name']}.\n"
            f"Цена плана: {plan['price_rub']} ₽/мес.\n"
            "Сейчас это тестовый switch без платежного шлюза."
        )

    def _period_key(self) -> str:
        return datetime.now().strftime("%Y-%m")

    def _active_plan_row(self, user_id: int):
        subscription = self.db.get_active_subscription(user_id)
        if not subscription:
            return None
        for plan in self.db.list_plans(only_active=False):
            if int(plan["id"]) == int(subscription["plan_id"]):
                return plan
        return None

    def _plan_features(self, user_id: int) -> tuple[dict[str, Any], Any]:
        plan = self._active_plan_row(user_id)
        if not plan:
            return {}, None
        return json.loads(plan["features_json"] or "{}"), plan

    def _current_month_window(self) -> tuple[datetime, datetime]:
        now = datetime.now()
        started_at = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if started_at.month == 12:
            finished_at = started_at.replace(year=started_at.year + 1, month=1)
        else:
            finished_at = started_at.replace(month=started_at.month + 1)
        return started_at, finished_at

    def _monthly_post_usage(self, user_id: int) -> int:
        started_at, finished_at = self._current_month_window()
        return self.db.count_jobs_for_user_in_period(
            user_id,
            started_at=started_at,
            finished_at=finished_at,
        )

    def _extra_post_allowance(self, user_id: int) -> int:
        return self.db.get_entitlement_quantity(
            user_id,
            kind="extra_post",
            period_key=self._period_key(),
        )

    def _extra_account_allowance(self, user_id: int, platform: str) -> int:
        return self.db.get_entitlement_quantity(
            user_id,
            kind="extra_account",
            period_key=self._period_key(),
            platform=platform,
        )

    def _account_usage_lines(self, user_id: int, base_limit: int) -> list[str]:
        lines: list[str] = []
        for platform in ["telegram", "vk", "instagram", "tiktok"]:
            used = self.db.count_accounts_for_user(user_id, platform=platform)
            extra = self._extra_account_allowance(user_id, platform)
            total_limit = base_limit + extra
            lines.append(f"• {self._platform_label(platform)}: {used} / {total_limit}")
        return lines

    def _enforce_account_creation_pricing(self, user_id: int, platform: str) -> None:
        user = self.db.get_user(user_id)
        if not user:
            raise ValueError("Пользователь не найден.")
        features, plan = self._plan_features(user_id)
        included_limit = int(features.get("accounts_per_platform", 0) or 0)
        purchased_extra = self._extra_account_allowance(user_id, platform)
        current_count = self.db.count_accounts_for_user(user_id, platform=platform)
        if current_count < included_limit + purchased_extra:
            return
        extra_price = int(features.get("extra_account_price_rub", 350))
        if int(user["credit_balance"]) < extra_price:
            raise ValueError(
                f"Лимит аккаунтов для {self._platform_label(platform)} исчерпан. "
                f"Нужно {extra_price} кредитов на доп. аккаунт, а доступно {user['credit_balance']}."
            )
        self.db.add_credit_transaction(
            user_id,
            -extra_price,
            "extra_account_purchase",
            {
                "platform": platform,
                "plan_code": plan["code"] if plan else None,
                "included_limit": included_limit,
                "purchased_extra": purchased_extra,
                "previous_count": current_count,
            },
        )

    def _charge_for_post_if_needed(self, user_id: int) -> str | None:
        user = self.db.get_user(user_id)
        if not user:
            raise ValueError("Пользователь не найден.")
        features, plan = self._plan_features(user_id)
        included_limit = int((plan["monthly_post_limit"] if plan else 0) or 0)
        used_posts = self._monthly_post_usage(user_id)
        purchased_extra = self._extra_post_allowance(user_id)
        if used_posts < included_limit + purchased_extra:
            return None
        extra_price = int(features.get("extra_post_price_rub", 35))
        if int(user["credit_balance"]) < extra_price:
            raise ValueError(
                f"Лимит постов за месяц исчерпан. Нужны доп. кредиты: {extra_price}. "
                f"Сейчас доступно {user['credit_balance']}."
            )
        self.db.add_credit_transaction(
            user_id,
            -extra_price,
            "extra_post_purchase",
            {
                "plan_code": plan["code"] if plan else None,
                "included_limit": included_limit,
                "purchased_extra": purchased_extra,
                "used_posts_before_charge": used_posts,
            },
        )
        remaining_balance = int(user["credit_balance"]) - extra_price
        return f"💳 Списано {extra_price} кредитов за доп. пост. Остаток: {remaining_balance}."

    def _addons_text(self, chat_id: int) -> str:
        user_id = self._current_user_id(chat_id)
        user = self.db.get_user(user_id)
        if not user:
            return "🧩 Допы пока недоступны."
        features, plan = self._plan_features(user_id)
        post_price = int(features.get("extra_post_price_rub", 35))
        account_price = int(features.get("extra_account_price_rub", 350))
        period_key = self._period_key()
        return "\n".join(
            [
                "🧩 Дополнительные возможности",
                f"Период: {period_key}",
                f"Баланс кредитов: {user['credit_balance']}",
                "",
                f"📝 Доп. пост: {post_price} кредитов",
                f"Уже куплено в этом месяце: {self._extra_post_allowance(user_id)}",
                "",
                f"🔗 Доп. аккаунт: {account_price} кредитов за 1 слот в выбранной соцсети",
                "Покупка действует для текущего месяца.",
                "",
                f"Текущий тариф: {plan['name'] if plan else 'Без тарифа'}",
            ]
        )

    def _purchase_extra_post(self, user_id: int) -> str:
        user = self.db.get_user(user_id)
        features, plan = self._plan_features(user_id)
        price = int(features.get("extra_post_price_rub", 35))
        if not user or int(user["credit_balance"]) < price:
            raise ValueError(f"Недостаточно кредитов для покупки доп. поста. Нужно {price}.")
        self.db.add_credit_transaction(
            user_id,
            -price,
            "extra_post_manual_purchase",
            {"plan_code": plan["code"] if plan else None},
        )
        self.db.add_entitlement(
            user_id,
            kind="extra_post",
            period_key=self._period_key(),
            quantity=1,
            metadata={"purchase_price": price},
        )
        updated_user = self.db.get_user(user_id)
        return f"✅ Куплен +1 доп. пост за {price} кредитов. Остаток: {updated_user['credit_balance']}."

    def _purchase_extra_account(self, user_id: int, platform: str) -> str:
        user = self.db.get_user(user_id)
        features, plan = self._plan_features(user_id)
        price = int(features.get("extra_account_price_rub", 350))
        if not user or int(user["credit_balance"]) < price:
            raise ValueError(f"Недостаточно кредитов для покупки доп. аккаунта. Нужно {price}.")
        self.db.add_credit_transaction(
            user_id,
            -price,
            "extra_account_manual_purchase",
            {"platform": platform, "plan_code": plan["code"] if plan else None},
        )
        self.db.add_entitlement(
            user_id,
            kind="extra_account",
            platform=platform,
            period_key=self._period_key(),
            quantity=1,
            metadata={"purchase_price": price},
        )
        updated_user = self.db.get_user(user_id)
        return (
            f"✅ Куплен +1 слот аккаунта для {self._platform_label(platform)} "
            f"за {price} кредитов. Остаток: {updated_user['credit_balance']}."
        )

    def _partner_text(self, chat_id: int) -> str:
        user_id = self._current_user_id(chat_id)
        user = self.db.get_user(user_id)
        if not user:
            return "🤝 Партнёрка пока недоступна."
        base = user["username"] or user["full_name"] or f"user{user_id}"
        code = self.db.get_or_create_referral_code(user_id, base)
        summary = self.db.get_referral_summary(user_id)
        return "\n".join(
            [
                "🤝 Партнёрская программа",
                f"Ваш код: {code}",
                f"Реферальная ссылка: https://t.me/{self._bot_username_guess()}?start=ref_{code}",
                "",
                f"Приглашено: {summary['invited_count']}",
                f"Начислено вознаграждений: {summary['total_rewards']}",
                "",
                "Следующий шаг: подключим обработку /start ref_CODE и автозачёт бонусов.",
            ]
        )

    def _bot_username_guess(self) -> str:
        if self.settings.telegram_bot_username:
            return self.settings.telegram_bot_username.lstrip("@")
        return "your_bot"

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

    def _set_instagram_token(self, text: str, received_at: datetime, chat_id: int) -> str:
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
            owner_user_id=self._current_user_id(chat_id),
        )
        return (
            "Instagram token updated in .env.\n"
            f"Obtained at: {self.settings.instagram_token_obtained_at}\n"
            f"Expires at: {self.settings.instagram_token_expires_at}\n"
            f"Updated Instagram accounts in DB: {updated_accounts}\n"
            "Use OAuth link from /instagram_token_status when you need to renew it again."
        )

    def _delete_account_command(self, text: str, chat_id: int) -> str:
        parts = text.split(maxsplit=1)
        if len(parts) != 2:
            raise ValueError("Формат: /delete_account <account_id>")
        account_id = int(parts[1])
        owner_user_id = self._current_user_id(chat_id)
        row = self.db.get_account(account_id, owner_user_id=owner_user_id)
        if not row:
            return f"Аккаунт #{account_id} не найден."
        self.db.delete_account(account_id, owner_user_id=owner_user_id)
        return f"Удалён аккаунт #{account_id}: {row['platform']} | {row['name']} | {row['destination']}"

    def _add_telegram_command(self, text: str, chat_id: int) -> str:
        _, name, destination = text.split(maxsplit=2)
        account_id = self._create_account(name, "telegram", destination, self._current_user_id(chat_id))
        return f"Добавлен Telegram-аккаунт #{account_id}: {name} -> {destination}"

    def _add_vk_command(self, text: str, chat_id: int) -> str:
        _, name, destination = text.split(maxsplit=2)
        account_id = self._create_account(name, "vk", destination, self._current_user_id(chat_id))
        return f"Добавлен VK-аккаунт #{account_id}: {name} -> {destination}"

    def _add_instagram_command(self, text: str, chat_id: int) -> str:
        _, name, username = text.split(maxsplit=2)
        account_id = self._create_account(name, "instagram", username, self._current_user_id(chat_id))
        return f"Добавлен Instagram-аккаунт #{account_id}: {name} -> {username}"

    def _add_tiktok_command(self, text: str, chat_id: int) -> str:
        _, name, username = text.split(maxsplit=2)
        account_id = self._create_account(name, "tiktok", username, self._current_user_id(chat_id))
        return f"Добавлен TikTok-аккаунт #{account_id}: {name} -> {username}"

    def _test_text_command(self, text: str, chat_id: int) -> str:
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            raise ValueError("Формат: /test_text <account_id> <text>")
        _, account_id_text, body = parts
        account_id = int(account_id_text)
        job = self._build_test_job(
            account_id=account_id,
            text=body,
            media_items=[],
            owner_user_id=self._current_user_id(chat_id),
        )
        return self._format_results(self.service.publish_job(job, dry_run=False))

    def _test_media_command(self, text: str, media_type: str, chat_id: int) -> str:
        parts = text.split(maxsplit=3)
        if len(parts) < 3:
            raise ValueError("Формат: /test_photo <account_id> <path_or_url> [caption]")
        _, account_id_text, source = parts[:3]
        caption = parts[3] if len(parts) >= 4 else ""
        account_id = int(account_id_text)
        owner_user_id = self._current_user_id(chat_id)
        row = self.db.get_account(account_id, owner_user_id=owner_user_id)
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
            owner_user_id=owner_user_id,
        )
        return self._format_results(self.service.publish_job(job, dry_run=False))

    def _build_test_job(
        self,
        account_id: int,
        text: str,
        media_items: list[MediaItem],
        owner_user_id: int,
    ) -> PostJob:
        row = self.db.get_account(account_id, owner_user_id=owner_user_id)
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
