from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock

from autoposter_bot.admin_bot import TelegramAdminBot
from autoposter_bot.db import Database
from autoposter_bot.models import MediaItem, OAuthConnection, PostJob, Target
from autoposter_bot.publishers.base import PublishResult
from autoposter_bot.service import AutoposterService
from autoposter_bot.spgutils_client import SpgUtilsClient


class RecordingPublisher:
    def __init__(self, platform: str = "tiktok") -> None:
        self.platform = platform
        self.calls: list[tuple[PostJob, Target, bool]] = []

    def publish(self, job: PostJob, target: Target, dry_run: bool = False) -> PublishResult:
        self.calls.append((job, target, dry_run))
        return PublishResult(self.platform, target.destination, True, "ok")


class FakeLinkService:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.requests: list[str] = []
        self.spgutils = SpgUtilsClient("https://api.spgutils.ru", "secret", timeout_seconds=20)

    def get_oauth_link_result(self, link_token: str) -> dict:
        self.requests.append(link_token)
        return self.result


class FakeWorkerClient:
    def __init__(self, connections: list[OAuthConnection]) -> None:
        self.connections = connections
        self.calls: list[int | None] = []
        self.token_calls: list[str] = []
        self.meta_page_calls: list[tuple[str, str]] = []

    def list_connections(
        self,
        telegram_user_id: int | None = None,
        provider: str | None = None,
    ) -> list[OAuthConnection]:
        self.calls.append(telegram_user_id)
        return self.connections

    def get_connection_token(self, connection_id: str | int) -> dict:
        self.token_calls.append(str(connection_id))
        return {"ok": True, "access_token": "worker-token", "scope": "posting"}

    def get_meta_page(self, connection_id: str | int, page_id: str | int) -> dict:
        self.meta_page_calls.append((str(connection_id), str(page_id)))
        return {
            "ok": True,
            "page": {
                "page_id": str(page_id),
                "ig_user_id": "ig-123",
                "destination": "@demo_page",
            },
        }


class OAuthWorkerFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = Path("tests") / ".tmp" / f"oauth-worker-{uuid.uuid4().hex}"
        self.tempdir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.tempdir / "autoposter.sqlite3"
        self.db = Database(self.db_path)
        self.db.init_schema()

    def tearDown(self) -> None:
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def _build_oauth_add_bot(self, user_id: int = 12345) -> tuple[TelegramAdminBot, Mock, list[tuple[str, dict]]]:
        bot = TelegramAdminBot.__new__(TelegramAdminBot)
        bot.db = self.db
        bot.settings = Mock(
            spgutils_api_base_url="https://api.spgutils.ru",
            spgutils_api_token="worker-token",
        )
        service = Mock()
        service.start_oauth_link.return_value = {
            "ok": True,
            "provider": "tiktok",
            "link_token": "link-123",
            "auth_url": "https://api.spgutils.ru/api/oauth/tiktok/start",
        }
        bot.service = service
        bot.chat_user_ids = {42: user_id}
        bot.sessions = {}
        bot.ui_message_ids = {}
        bot.ui_edit_targets = {}
        sent_messages: list[tuple[str, dict]] = []
        bot._safe_send_message = lambda chat_id, text, reply_markup=None, ui=False: sent_messages.append(
            (
                str(text),
                {
                    "chat_id": chat_id,
                    "reply_markup": reply_markup,
                    "ui": ui,
                },
            )
        )
        return bot, service, sent_messages

    def test_start_oauth_link(self) -> None:
        client = SpgUtilsClient("https://api.spgutils.ru", "worker-token", timeout_seconds=20)
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "ok": True,
            "provider": "tiktok",
            "link_token": "link-123",
            "auth_url": "https://api.spgutils.ru/api/oauth/tiktok/start",
        }
        client.http.request = Mock(return_value=response)

        payload = client.start_oauth_link("tiktok", 12345, 67890)

        self.assertEqual(payload["link_token"], "link-123")
        client.http.request.assert_called_once_with(
            "POST",
            "https://api.spgutils.ru/api/link/start",
            params=None,
            json={
                "telegram_user_id": "12345",
                "telegram_chat_id": "67890",
                "provider": "tiktok",
            },
            headers={
                "Accept": "application/json",
                "Authorization": "Bearer worker-token",
                "Content-Type": "application/json",
            },
            timeout=20,
        )

    def test_link_result_connected(self) -> None:
        client = SpgUtilsClient("https://api.spgutils.ru", "worker-token", timeout_seconds=20)
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "ok": True,
            "link": {
                "link_token": "link-abc",
                "provider": "tiktok",
                "telegram_user_id": "55555",
                "status": "connected",
                "connection_id": 7,
            },
            "connection": {
                "id": 7,
                "provider": "tiktok",
                "provider_user_id": "creator-77",
                "telegram_user_id": "55555",
                "link_token": "link-abc",
                "scopes": "posting",
                "revoked": 0,
            },
        }
        client.http.request = Mock(return_value=response)

        payload = client.get_link_result("link-abc")

        self.assertTrue(payload["ok"])
        client.http.request.assert_called_once_with(
            "GET",
            "https://api.spgutils.ru/api/link/result",
            params={"link_token": "link-abc"},
            json=None,
            headers={
                "Accept": "application/json",
                "Authorization": "Bearer worker-token",
            },
            timeout=20,
        )

    def test_oauth_done_deeplink(self) -> None:
        user = self.db.ensure_user(55555, "oauth_user", "OAuth User")
        owner_user_id = int(user["id"])
        bot = TelegramAdminBot.__new__(TelegramAdminBot)
        bot.db = self.db
        bot.service = FakeLinkService(
            {
                "ok": True,
                "link": {
                    "link_token": "link-abc",
                    "provider": "tiktok",
                    "telegram_user_id": "55555",
                    "status": "connected",
                    "connection_id": 7,
                },
                "connection": {
                    "id": 7,
                    "provider": "tiktok",
                    "provider_user_id": "creator-77",
                    "telegram_user_id": "55555",
                    "link_token": "link-abc",
                    "scopes": "posting",
                    "revoked": 0,
                    "account_name": "Demo TikTok",
                    "destination": "@demo_tiktok",
                },
            }
        )
        bot.chat_user_ids = {42: owner_user_id}
        bot.sessions = {}
        bot.ui_message_ids = {}
        bot.ui_edit_targets = {}
        sent_messages: list[tuple[str, dict]] = []
        bot._safe_send_message = lambda chat_id, text, reply_markup=None, ui=False: sent_messages.append(
            (
                str(text),
                {
                    "chat_id": chat_id,
                    "reply_markup": reply_markup,
                    "ui": ui,
                },
            )
        )

        reply = bot._handle_oauth_done_start_payload(42, "/start oauth_done_link-abc")

        self.assertEqual(reply, "Аккаунт успешно подключён.")
        rows = self.db.list_accounts(owner_user_id=owner_user_id)
        self.assertEqual(len(rows), 1)
        options = self.db.resolve_account_options(int(rows[0]["id"]), owner_user_id=owner_user_id)
        self.assertEqual(options["oauth_connection_id"], "7")
        self.assertEqual(options["oauth_provider"], "tiktok")
        self.assertGreaterEqual(len(sent_messages), 1)

    def test_add_tiktok_account_starts_oauth_link(self) -> None:
        bot, service, sent_messages = self._build_oauth_add_bot()

        reply = bot._dispatch_account_callback(42, ["acct", "add", "tiktok"])

        self.assertIsNone(reply)
        service.start_oauth_link.assert_called_once_with(12345, 42, "tiktok")
        self.assertEqual(bot.sessions, {})
        self.assertTrue(any("TikTok через OAuth" in message for message, _ in sent_messages))
        self.assertTrue(any("Подключить TikTok" in str(payload["reply_markup"]) for _, payload in sent_messages))
        self.assertTrue(all("Введите название аккаунта" not in message for message, _ in sent_messages))

    def test_add_instagram_account_starts_meta_oauth_link(self) -> None:
        bot, service, sent_messages = self._build_oauth_add_bot()

        reply = bot._dispatch_account_callback(42, ["acct", "add", "instagram"])

        self.assertIsNone(reply)
        service.start_oauth_link.assert_called_once_with(12345, 42, "meta")
        self.assertEqual(bot.sessions, {})
        self.assertTrue(any("Instagram/Meta через OAuth" in message for message, _ in sent_messages))
        self.assertTrue(any("Подключить Instagram" in str(payload["reply_markup"]) for _, payload in sent_messages))
        self.assertTrue(all("Введите название аккаунта" not in message for message, _ in sent_messages))

    def test_tiktok_does_not_ask_for_manual_account_name(self) -> None:
        bot, service, sent_messages = self._build_oauth_add_bot()

        bot._dispatch_account_callback(42, ["acct", "add", "tiktok"])

        self.assertEqual(service.start_oauth_link.call_count, 1)
        self.assertEqual(bot.sessions, {})
        self.assertTrue(all("Введите название аккаунта" not in message for message, _ in sent_messages))

    def test_instagram_does_not_ask_for_manual_account_name(self) -> None:
        bot, service, sent_messages = self._build_oauth_add_bot()

        bot._dispatch_account_callback(42, ["acct", "add", "instagram"])

        self.assertEqual(service.start_oauth_link.call_count, 1)
        self.assertEqual(bot.sessions, {})
        self.assertTrue(all("Введите название аккаунта" not in message for message, _ in sent_messages))

    def test_oauth_add_shows_config_error_when_worker_not_configured(self) -> None:
        bot, service, sent_messages = self._build_oauth_add_bot()
        bot.settings = Mock(spgutils_api_base_url="https://api.spgutils.ru", spgutils_api_token=None)

        reply = bot._dispatch_account_callback(42, ["acct", "add", "tiktok"])

        self.assertEqual(reply, "OAuth-подключение не настроено. Проверьте SPGUTILS_API_BASE_URL и SPGUTILS_API_TOKEN.")
        self.assertEqual(service.start_oauth_link.call_count, 0)
        self.assertEqual(bot.sessions, {})
        self.assertEqual(sent_messages, [])

    def test_start_oauth_done_deeplink_through_update(self) -> None:
        user = self.db.ensure_user(66666, "oauth_user2", "OAuth User 2")
        owner_user_id = int(user["id"])
        bot = TelegramAdminBot.__new__(TelegramAdminBot)
        bot.db = self.db
        bot.service = FakeLinkService(
            {
                "ok": True,
                "link": {
                    "link_token": "link-xyz",
                    "provider": "meta",
                    "telegram_user_id": "66666",
                    "status": "connected",
                    "connection_id": 17,
                },
                "connection": {
                    "id": 17,
                    "provider": "meta",
                    "provider_user_id": "page-17",
                    "telegram_user_id": "66666",
                    "link_token": "link-xyz",
                    "scopes": "pages_show_list",
                    "revoked": 0,
                    "account_name": "Demo Page",
                    "destination": "@demo_page",
                },
            }
        )
        bot.chat_user_ids = {42: owner_user_id}
        bot.sessions = {}
        bot.ui_message_ids = {}
        bot.ui_edit_targets = {}
        bot._is_allowed = lambda user_id: True
        reset_calls: list[int] = []
        main_menu_calls: list[int] = []
        sent_messages: list[tuple[str, dict]] = []
        bot._reset_session = lambda chat_id: reset_calls.append(chat_id)
        bot._send_main_menu = lambda chat_id: main_menu_calls.append(chat_id)
        bot._safe_send_message = lambda chat_id, text, reply_markup=None, ui=False: sent_messages.append(
            (
                str(text),
                {
                    "chat_id": chat_id,
                    "reply_markup": reply_markup,
                    "ui": ui,
                },
            )
        )

        bot._handle_update(
            {
                "update_id": 1,
                "message": {
                    "message_id": 11,
                    "date": 1710000000,
                    "chat": {"id": 42},
                    "from": {"id": 66666, "username": "oauth_user2", "first_name": "OAuth"},
                    "text": "/start oauth_done_link-xyz",
                },
            }
        )

        self.assertEqual(reset_calls, [])
        self.assertEqual(main_menu_calls, [])
        self.assertGreaterEqual(len(sent_messages), 2)
        self.assertTrue(any("Аккаунт успешно подключён" in message for message, _ in sent_messages))
        rows = self.db.list_accounts(owner_user_id=owner_user_id)
        self.assertEqual(len(rows), 1)
        options = self.db.resolve_account_options(int(rows[0]["id"]), owner_user_id=owner_user_id)
        self.assertEqual(options["oauth_connection_id"], "17")
        self.assertEqual(options["oauth_provider"], "meta")

    def test_sync_connections_by_telegram_user_id(self) -> None:
        user = self.db.ensure_user(44444, "sync_user", "Sync User")
        owner_user_id = int(user["id"])
        self.db.complete_user_registration(owner_user_id)
        service = AutoposterService.__new__(AutoposterService)
        service.db = self.db
        service.spgutils = FakeWorkerClient(
            [
                OAuthConnection(
                    remote_connection_id="conn-55",
                    provider="tiktok",
                    telegram_user_id=44444,
                    account_name="Sync TikTok",
                    destination="@sync_tiktok",
                    status="connected",
                    provider_user_id="creator-55",
                    scopes="posting",
                )
            ]
        )

        synced = service.sync_oauth_connections_for_user(owner_user_id)

        self.assertEqual(synced, 1)
        self.assertEqual(service.spgutils.calls, [44444])
        rows = self.db.list_accounts(owner_user_id=owner_user_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["platform"], "tiktok")

    def test_get_token_before_publish(self) -> None:
        user = self.db.ensure_user(33333, "publisher", "Publisher")
        owner_user_id = int(user["id"])
        self.db.complete_user_registration(owner_user_id)
        account_id = self.db.add_account(
            name="Token TikTok",
            platform="tiktok",
            destination="@token_tiktok",
            options={"oauth_connection_id": "conn-100"},
            owner_user_id=owner_user_id,
        )
        service = AutoposterService.__new__(AutoposterService)
        service.db = self.db
        service.spgutils = FakeWorkerClient([])
        recorder = RecordingPublisher("tiktok")
        service.publishers = {"tiktok": recorder}
        job = PostJob(
            post_id="job-1",
            content_type="tiktok_video",
            text="Hello",
            media_items=[MediaItem("https://example.com/video.mp4", "video")],
            targets=[
                Target(
                    platform="tiktok",
                    destination="@token_tiktok",
                    account_id=account_id,
                    account_name="Token TikTok",
                )
            ],
            metadata={"owner_user_id": owner_user_id},
        )

        results = service.publish_job(job, dry_run=False)

        self.assertTrue(results[0].ok)
        self.assertEqual(service.spgutils.token_calls, ["conn-100"])
        self.assertEqual(recorder.calls[0][1].options["access_token"], "worker-token")

    def test_no_tokens_in_logs(self) -> None:
        bot = TelegramAdminBot.__new__(TelegramAdminBot)
        redacted = bot._redact_text("/start oauth_done_link-abc access_token=secret refresh_token=secret")
        self.assertNotIn("secret", redacted)
        self.assertIn("oauth_done_[REDACTED]", redacted)


if __name__ == "__main__":
    unittest.main()
