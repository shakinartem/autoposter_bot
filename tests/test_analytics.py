from __future__ import annotations

from datetime import datetime, timedelta, timezone

from autoposter_bot.analytics.base import CollectorRegistry
from autoposter_bot.application.analytics import AnalyticsCollectionService
from autoposter_bot.application.content import ContentApplication
from autoposter_bot.db import Database
from autoposter_bot.domain.content import Publication, PublicationStatus
from autoposter_bot.infrastructure.analytics_store import AnalyticsStore
from autoposter_bot.infrastructure.content_store import SQLiteContentStore
from autoposter_bot.infrastructure.workspace_schema import init_workspace_schema
from autoposter_bot.infrastructure.workspace_store import WorkspaceContentStore


class FakeInstagramCollector:
    platform = "instagram"
    version = "fake-instagram-v1"

    def __init__(self) -> None:
        self.calls = 0

    def collect(self, publication, *, account_options):
        self.calls += 1
        assert account_options["access_token"] == "secret"
        return {
            "collection_status": "ok",
            "views": 123,
            "reach": 88,
            "saved": 4,
        }


def _runtime(tmp_path):
    db_path = tmp_path / "analytics.sqlite3"
    Database(db_path).init_schema()
    raw = SQLiteContentStore(db_path)
    raw.init_schema()
    init_workspace_schema(raw)
    analytics = AnalyticsStore(backend="sqlite", connect=raw.connect)
    analytics.init_schema()
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    with raw.connect() as db:
        db.execute(
            "INSERT INTO users(id, telegram_user_id, username, full_name, is_active, created_at) VALUES (1, 101, 'owner', 'Owner', 1, ?)",
            (now,),
        )
        db.execute(
            "INSERT INTO users(id, telegram_user_id, username, full_name, is_active, created_at) VALUES (2, 202, 'other', 'Other', 1, ?)",
            (now,),
        )
        db.execute(
            "INSERT INTO workspaces(id, name, owner_user_id, created_at) VALUES (5, 'Alpha', 1, ?)",
            (now,),
        )
        db.execute(
            "INSERT INTO workspaces(id, name, owner_user_id, created_at) VALUES (6, 'Beta', 2, ?)",
            (now,),
        )
        account_id = int(db.execute(
            "INSERT INTO accounts(owner_user_id, name, platform, destination, options_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (1, "Instagram", "instagram", "@alpha", '{"access_token":"secret","api_flow":"instagram_login"}', now),
        ).lastrowid)
        db.execute(
            "INSERT INTO workspace_accounts(workspace_id, account_id, created_at) VALUES (?, ?, ?)",
            (5, account_id, now),
        )
    return raw, analytics, account_id


def _published(raw, account_id: int, *, published_at: datetime, remote_id: str | None = "media-1"):
    scoped = WorkspaceContentStore(raw, 5)
    app = ContentApplication(scoped)
    content = app.create(
        title="Master headline",
        body="Master body",
        hashtags=["autoposter", "intent"],
        workspace_id=5,
    )
    variant = app.upsert_variant(
        content.id,
        "instagram",
        title="IG headline",
        text="IG body",
        fields={"content_type": "instagram_feed_image"},
    )
    assert variant is not None
    publication = Publication(
        variant_id=variant.id,
        platform="instagram",
        account_id=account_id,
        destination="@alpha",
        scheduled_at=published_at - timedelta(minutes=5),
        status=PublicationStatus.PUBLISHED,
        external_post_id=remote_id,
        external_url="https://instagram.example/post",
        published_at=published_at,
    )
    scoped.save_publication(publication)
    return publication


def test_snapshot_is_append_only_unique_and_workspace_scoped(tmp_path):
    raw, analytics, account_id = _runtime(tmp_path)
    published_at = datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc)
    publication = _published(raw, account_id, published_at=published_at)

    assert analytics.append(
        publication_id=publication.id,
        platform="instagram",
        milestone="1h",
        collector_version="collector-v1",
        metrics={"views": 10},
        captured_at=published_at + timedelta(hours=1),
    ) is True
    assert analytics.append(
        publication_id=publication.id,
        platform="instagram",
        milestone="1h",
        collector_version="collector-v1",
        metrics={"views": 999},
        captured_at=published_at + timedelta(hours=1, minutes=1),
    ) is False

    alpha = analytics.list_for_publication(workspace_id=5, publication_id=publication.id)
    beta = analytics.list_for_publication(workspace_id=6, publication_id=publication.id)
    assert len(alpha) == 1
    assert alpha[0].metrics["views"] == 10
    assert beta == []

    dataset = analytics.dataset(workspace_id=5)
    assert len(dataset) == 1
    assert dataset[0]["master_title"] == "Master headline"
    assert dataset[0]["variant_text"] == "IG body"
    assert dataset[0]["metrics"]["views"] == 10
    assert analytics.dataset(workspace_id=6) == []


def test_worker_collects_live_milestone_and_marks_old_windows_missed(tmp_path):
    raw, analytics, account_id = _runtime(tmp_path)
    published_at = datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc)
    publication = _published(raw, account_id, published_at=published_at)
    collector = FakeInstagramCollector()
    service = AnalyticsCollectionService(
        analytics=analytics,
        content_store=raw,
        collectors=CollectorRegistry([collector]),
    )

    first = service.run_once(now=published_at + timedelta(hours=1, minutes=10))
    assert first["collected"] == 1
    assert collector.calls == 1

    duplicate = service.run_once(now=published_at + timedelta(hours=1, minutes=20))
    assert duplicate["collected"] == 0
    assert collector.calls == 1

    later = service.run_once(now=published_at + timedelta(hours=25))
    assert later["missed"] == 1  # 6h window is too old
    assert later["collected"] == 1  # 24h is still inside its tolerance
    assert collector.calls == 2

    snapshots = analytics.list_for_publication(workspace_id=5, publication_id=publication.id)
    by_milestone = {item.milestone: item for item in snapshots}
    assert by_milestone["1h"].metrics["views"] == 123
    assert by_milestone["6h"].metrics["collection_status"] == "missed"
    assert by_milestone["24h"].metrics["views"] == 123
    assert by_milestone["24h"].metrics["capture_lag_seconds"] == 3600


def test_missing_remote_id_becomes_explicit_data_gap_not_infinite_retry(tmp_path):
    raw, analytics, account_id = _runtime(tmp_path)
    published_at = datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc)
    publication = _published(raw, account_id, published_at=published_at, remote_id=None)
    collector = FakeInstagramCollector()
    service = AnalyticsCollectionService(
        analytics=analytics,
        content_store=raw,
        collectors=CollectorRegistry([collector]),
    )

    stats = service.run_once(now=published_at + timedelta(hours=1, minutes=5))

    assert stats["missed"] == 1
    assert collector.calls == 0
    snapshot = analytics.list_for_publication(workspace_id=5, publication_id=publication.id)[0]
    assert snapshot.metrics["collection_status"] == "unavailable"
    assert snapshot.metrics["reason"] == "publication_has_no_external_post_id"
