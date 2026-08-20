from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from autoposter_bot.infrastructure.account_health_store import AccountHealthStore
from autoposter_bot.infrastructure.operations_store import OperationsStore
from autoposter_bot.infrastructure.postgres_store import PostgresContentStore
from autoposter_bot.infrastructure.retry_schema import init_retry_schema


POSTGRES_URL = os.getenv("AUTOPOSTER_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="PostgreSQL integration URL is not configured")


def test_postgres_account_health_is_workspace_scoped_and_feeds_operations():
    assert POSTGRES_URL is not None
    root = PostgresContentStore(POSTGRES_URL)
    account_health = AccountHealthStore(backend="postgres", connect=root.connect)
    operations = OperationsStore(backend="postgres", connect=root.connect)
    try:
        root.init_schema()
        init_retry_schema(backend="postgres", connect=root.connect)
        account_health.init_schema()
        operations.init_schema()
        now = datetime.now(timezone.utc)
        naive_now = now.replace(tzinfo=None)
        with root.connect() as db:
            db.execute("TRUNCATE TABLE users CASCADE")
            db.execute(
                "INSERT INTO users(id, telegram_user_id, username, full_name, is_registered, created_at) VALUES (%s, %s, %s, %s, TRUE, %s)",
                (8101, 98101, "owner-health", "Health Owner", naive_now),
            )
            workspace_id = int(db.execute(
                "INSERT INTO workspaces(name, owner_user_id, created_at) VALUES (%s, %s, %s) RETURNING id",
                ("Account Health", 8101, naive_now),
            ).fetchone()["id"])
            account_id = int(db.execute(
                "INSERT INTO accounts(owner_user_id, name, platform, destination, options_json, created_at) VALUES (%s, %s, %s, %s, %s::jsonb, %s) RETURNING id",
                (8101, "TikTok Health", "tiktok", "@health", '{}', naive_now),
            ).fetchone()["id"])

        stored = account_health.upsert(
            workspace_id=workspace_id,
            account_id=account_id,
            platform="tiktok",
            status="critical",
            code="credential_rejected",
            message="Reconnect required",
            probe_method="tiktok.user.info",
            reconnect_required=True,
            identity={},
            checked_at=now,
        )
        assert stored["account_id"] == account_id
        assert stored["reconnect_required"] is True

        overview = operations.workspace_overview(workspace_id=workspace_id, now=now)
        assert overview["social_connections"]["critical"] == 1
        assert overview["social_connections"]["reconnect_required"] == 1
        assert overview["health"] == "critical"
        assert any(item["code"] == "social_connection_health" for item in overview["health_reasons"])
    finally:
        root.close()
