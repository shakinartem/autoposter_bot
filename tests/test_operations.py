from __future__ import annotations

from datetime import datetime, timedelta, timezone

from autoposter_bot.application.content import ContentApplication
from autoposter_bot.domain.content import Publication, PublicationStatus
from autoposter_bot.infrastructure.analytics_store import AnalyticsStore
from autoposter_bot.infrastructure.content_store import SQLiteContentStore
from autoposter_bot.infrastructure.operations_store import OperationsStore
from autoposter_bot.infrastructure.publication_tracking_schema import init_publication_tracking_schema
from autoposter_bot.infrastructure.retry_schema import init_retry_schema


def _setup(tmp_path):
    store = SQLiteContentStore(tmp_path / "ops.db")
    store.init_schema()
    init_retry_schema(backend="sqlite", connect=store.connect)
    init_publication_tracking_schema(backend="sqlite", connect=store.connect)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with store.connect() as db:
        db.execute(
            "INSERT INTO users(telegram_user_id, username, full_name, is_registered, created_at) VALUES (?, ?, ?, ?, ?)",
            (771001, "owner", "Owner", 1, now.isoformat()),
        )
        user_id = int(db.execute("SELECT id FROM users WHERE telegram_user_id = ?", (771001,)).fetchone()["id"])
        workspace_id = int(db.execute(
            "INSERT INTO workspaces(name, owner_user_id, created_at) VALUES (?, ?, ?)",
            ("Ops", user_id, now.isoformat()),
        ).lastrowid)
        account_id = int(db.execute(
            "INSERT INTO accounts(owner_user_id, name, platform, destination, options_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, "Telegram", "telegram", "-1001", "{}", now.isoformat()),
        ).lastrowid)
    scoped = store
    content = ContentApplication(scoped).create(title="Ops", body="Body", workspace_id=workspace_id)
    variant = ContentApplication(scoped).upsert_variant(content.id, "telegram")
    assert variant is not None
    return store, workspace_id, account_id, variant, now


def test_workspace_operations_reports_queue_lag_and_quarantine(tmp_path):
    store, workspace_id, account_id, variant, now = _setup(tmp_path)
    due_at = now - timedelta(minutes=10)
    publication = Publication(
        variant_id=variant.id,
        platform="telegram",
        account_id=account_id,
        scheduled_at=due_at,
        status=PublicationStatus.SCHEDULED,
    )
    store.save_publication(publication)
    failed = Publication(
        variant_id=variant.id,
        platform="telegram",
        account_id=account_id,
        status=PublicationStatus.FAILED,
        last_error_code="unknown_publish_outcome",
    )
    store.save_publication(failed)

    overview = OperationsStore(backend="sqlite", connect=store.connect).workspace_overview(
        workspace_id=workspace_id,
        now=now.replace(tzinfo=timezone.utc),
    )

    assert overview["health"] == "critical"
    assert overview["queue"]["due_count"] == 1
    assert overview["queue"]["lag_seconds"] >= 600
    assert overview["reconciliation"]["unknown_outcomes"] == 1
    assert overview["publication_statuses"]["scheduled"] == 1
    assert overview["publication_statuses"]["failed"] == 1
