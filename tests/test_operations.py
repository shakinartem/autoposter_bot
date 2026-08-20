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


def test_reconciliation_recovery_is_workspace_scoped_and_audited(tmp_path):
    store, workspace_id, account_id, variant, now = _setup(tmp_path)
    operations = OperationsStore(backend="sqlite", connect=store.connect)
    operations.init_schema()
    failed = Publication(
        variant_id=variant.id,
        platform="telegram",
        account_id=account_id,
        status=PublicationStatus.FAILED,
        last_error_code="unknown_publish_outcome",
        last_error_message="ambiguous remote result",
    )
    store.save_publication(failed)

    items = operations.list_reconciliation_items(workspace_id=workspace_id)
    assert [item["id"] for item in items] == [failed.id]
    assert items[0]["can_retry"] is True

    resolved = operations.resolve_publication(
        workspace_id=workspace_id,
        publication_id=failed.id,
        action="retry",
        actor_user_id=None,
        note="Checked remote platform: post is absent",
        acknowledge_duplicate_risk=True,
        now=now.replace(tzinfo=timezone.utc),
    )
    assert resolved["status"] == "scheduled"
    assert resolved["last_error_code"] is None
    assert operations.list_reconciliation_items(workspace_id=workspace_id) == []

    events = operations.list_events(workspace_id=workspace_id)
    assert len(events) == 1
    assert events[0]["event_type"] == "publication_reconciliation.retry"
    assert events[0]["details"]["previous"]["status"] == "failed"
    assert events[0]["details"]["acknowledge_duplicate_risk"] is True


def test_retry_requires_ack_and_is_blocked_when_remote_identity_exists(tmp_path):
    store, workspace_id, account_id, variant, now = _setup(tmp_path)
    operations = OperationsStore(backend="sqlite", connect=store.connect)
    operations.init_schema()
    failed = Publication(
        variant_id=variant.id,
        platform="tiktok",
        account_id=account_id,
        status=PublicationStatus.FAILED,
        provider_tracking_id="v_pub_file~123",
        last_error_code="unknown_publish_outcome",
    )
    store.save_publication(failed)

    try:
        operations.resolve_publication(
            workspace_id=workspace_id,
            publication_id=failed.id,
            action="retry",
            actor_user_id=None,
            note="Force retry",
            acknowledge_duplicate_risk=False,
            now=now.replace(tzinfo=timezone.utc),
        )
    except ValueError as exc:
        assert "acknowledge_duplicate_risk" in str(exc)
    else:
        raise AssertionError("retry without acknowledgement must be rejected")

    try:
        operations.resolve_publication(
            workspace_id=workspace_id,
            publication_id=failed.id,
            action="retry",
            actor_user_id=None,
            note="Checked provider",
            acknowledge_duplicate_risk=True,
            now=now.replace(tzinfo=timezone.utc),
        )
    except ValueError as exc:
        assert "remote identity" in str(exc)
    else:
        raise AssertionError("retry with provider tracking id must be rejected")


def test_confirm_published_requires_final_remote_id_and_clears_quarantine(tmp_path):
    store, workspace_id, account_id, variant, now = _setup(tmp_path)
    operations = OperationsStore(backend="sqlite", connect=store.connect)
    operations.init_schema()
    processing = Publication(
        variant_id=variant.id,
        platform="tiktok",
        account_id=account_id,
        status=PublicationStatus.PROCESSING,
        provider_tracking_id="v_pub_file~999",
        last_error_code="upload_interrupted",
    )
    store.save_publication(processing)

    try:
        operations.resolve_publication(
            workspace_id=workspace_id,
            publication_id=processing.id,
            action="confirm_published",
            actor_user_id=None,
            note="Provider shows published",
            now=now.replace(tzinfo=timezone.utc),
        )
    except ValueError as exc:
        assert "external_post_id" in str(exc)
    else:
        raise AssertionError("manual published confirmation without final id must be rejected")

    resolved = operations.resolve_publication(
        workspace_id=workspace_id,
        publication_id=processing.id,
        action="confirm_published",
        actor_user_id=None,
        note="Verified in provider dashboard",
        external_post_id="7400123456789012345",
        external_url="https://www.tiktok.com/@creator/video/7400123456789012345",
        now=now.replace(tzinfo=timezone.utc),
    )
    assert resolved["status"] == "published"
    assert resolved["external_post_id"] == "7400123456789012345"
    assert resolved["last_error_code"] is None
    assert resolved["can_retry"] is False


def test_reconciliation_cannot_cross_workspace_boundary(tmp_path):
    store, workspace_id, account_id, variant, now = _setup(tmp_path)
    operations = OperationsStore(backend="sqlite", connect=store.connect)
    operations.init_schema()
    failed = Publication(
        variant_id=variant.id,
        platform="telegram",
        account_id=account_id,
        status=PublicationStatus.FAILED,
        last_error_code="unknown_publish_outcome",
    )
    store.save_publication(failed)

    assert operations.list_reconciliation_items(workspace_id=workspace_id + 999) == []
    try:
        operations.resolve_publication(
            workspace_id=workspace_id + 999,
            publication_id=failed.id,
            action="mark_failed",
            actor_user_id=None,
            note="Should not see this publication",
            now=now.replace(tzinfo=timezone.utc),
        )
    except KeyError:
        pass
    else:
        raise AssertionError("cross-workspace recovery must not be possible")


def test_stale_processing_uses_processing_start_not_last_poll(tmp_path):
    store, workspace_id, account_id, variant, now = _setup(tmp_path)
    operations = OperationsStore(backend="sqlite", connect=store.connect)
    operations.init_schema()
    processing = Publication(
        variant_id=variant.id,
        platform="tiktok",
        account_id=account_id,
        status=PublicationStatus.PROCESSING,
        provider_tracking_id="v_pub_file~stale",
        metadata={"processing_started_at": (now - timedelta(hours=2)).isoformat()},
    )
    store.save_publication(processing)
    # Simulate a recent status poll. updated_at is fresh, but processing itself
    # started two hours ago and must still be surfaced as stale.
    with store.connect() as db:
        db.execute(
            "UPDATE publications_v2 SET updated_at = ? WHERE id = ?",
            (now.isoformat(), processing.id),
        )

    overview = operations.workspace_overview(
        workspace_id=workspace_id,
        now=now.replace(tzinfo=timezone.utc),
    )
    assert overview["reconciliation"]["stale_processing"] == 1
    assert overview["health"] == "critical"
    assert any(reason["code"] == "stale_provider_processing" for reason in overview["health_reasons"])
