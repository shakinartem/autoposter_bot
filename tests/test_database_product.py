from __future__ import annotations

import unittest
import uuid
import shutil
from pathlib import Path

from autoposter_bot.db import Database


class DatabaseProductTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = Path("tests") / ".tmp" / f"db-{uuid.uuid4().hex}"
        self.tempdir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.tempdir / "test.sqlite3"
        self.db = Database(self.db_path)
        self.db.init_schema()

    def tearDown(self) -> None:
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_new_user_is_unregistered_until_completed(self) -> None:
        user = self.db.ensure_user(12345, "demo_user", "Demo User")
        self.assertEqual(int(user["is_registered"]), 0)

        self.db.complete_user_registration(int(user["id"]), full_name="Registered Demo")
        updated = self.db.get_user(int(user["id"]))

        self.assertIsNotNone(updated)
        self.assertEqual(int(updated["is_registered"]), 1)
        self.assertEqual(updated["full_name"], "Registered Demo")
        self.assertIsNotNone(updated["registered_at"])

    def test_notifications_can_be_deduplicated_and_listed(self) -> None:
        user = self.db.ensure_user(22222, "notify_user", "Notify User")
        user_id = int(user["id"])
        self.db.complete_user_registration(user_id)

        first_id = self.db.add_notification(
            user_id,
            kind="vk_token_expiry",
            title="VK token скоро истечёт",
            body="Тестовое уведомление",
            dedupe_key="vk:2026-04-20T12:00:00",
        )
        second_id = self.db.add_notification(
            user_id,
            kind="vk_token_expiry",
            title="VK token скоро истечёт",
            body="Тестовое уведомление",
            dedupe_key="vk:2026-04-20T12:00:00",
        )

        notifications = self.db.list_notifications(user_id, limit=10)
        self.assertIsNotNone(first_id)
        self.assertIsNone(second_id)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(self.db.count_unread_notifications(user_id), 1)

    def test_publish_events_are_recorded(self) -> None:
        user = self.db.ensure_user(33333, "publisher", "Publisher")
        user_id = int(user["id"])
        self.db.complete_user_registration(user_id)

        self.db.add_publish_event(
            user_id,
            platform="telegram",
            destination="@demo_channel",
            status="ok",
            detail="Posted successfully",
            external_post_id="post-1",
        )

        events = self.db.list_publish_events(user_id, limit=10)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["platform"], "telegram")
        self.assertEqual(events[0]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
