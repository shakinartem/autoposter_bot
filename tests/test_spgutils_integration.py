from __future__ import annotations

import shutil
import unittest
from pathlib import Path
import uuid

from autoposter_bot.db import Database
from autoposter_bot.models import OAuthConnection
from autoposter_bot.spgutils_client import SpgUtilsClient


class SpgUtilsIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = Path("tests") / ".tmp" / f"spgutils-{uuid.uuid4().hex}"
        self.tempdir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.tempdir / "autoposter.sqlite3"
        self.db = Database(self.db_path)
        self.db.init_schema()

    def tearDown(self) -> None:
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_parse_connection_payload(self) -> None:
        client = SpgUtilsClient("https://api.spgutils.ru")
        payload = {
            "connection_id": "conn-123",
            "platform": "tiktok",
            "telegram_user_id": 555,
            "account_name": "demo account",
            "destination": "@demo",
            "access_token": "secret-token",
            "status": "active",
            "metadata": {"region": "eu"},
        }

        connection = client._parse_connection(payload)

        self.assertEqual(connection.connection_key, "conn-123")
        self.assertEqual(connection.platform, "tiktok")
        self.assertEqual(connection.telegram_user_id, 555)
        self.assertEqual(connection.access_token, "secret-token")
        self.assertEqual(connection.metadata["region"], "eu")

    def test_sync_oauth_connection_hydrates_account_options(self) -> None:
        user = self.db.ensure_user(44444, "oauth_user", "OAuth User")
        owner_user_id = int(user["id"])
        self.db.complete_user_registration(owner_user_id)
        account_id = self.db.add_account(
            name="demo instagram",
            platform="instagram",
            destination="@demo",
            options={"oauth_connection_key": "conn-123"},
            owner_user_id=owner_user_id,
        )

        self.db.sync_oauth_connections(
            [
                OAuthConnection(
                    remote_connection_id="conn-123",
                    provider="instagram",
                    telegram_user_id=44444,
                    account_name="demo instagram",
                    destination="@demo",
                    access_token="secret-token",
                    status="active",
                    metadata={"page_id": "page-1"},
                )
            ],
            owner_user_id=owner_user_id,
        )

        rows = self.db.list_oauth_connections(owner_user_id=owner_user_id)
        self.assertEqual(len(rows), 1)
        resolved = self.db.resolve_account_options(account_id, owner_user_id=owner_user_id)
        self.assertEqual(resolved["access_token"], "secret-token")
        self.assertEqual(resolved["oauth_connection_key"], "conn-123")


if __name__ == "__main__":
    unittest.main()
