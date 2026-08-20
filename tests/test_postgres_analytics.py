from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from autoposter_bot.application.content import ContentApplication
from autoposter_bot.domain.content import Publication, PublicationStatus
from autoposter_bot.infrastructure.analytics_store import AnalyticsStore
from autoposter_bot.infrastructure.postgres_store import PostgresContentStore

POSTGRES_URL = os.getenv("AUTOPOSTER_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="PostgreSQL integration URL is not configured")


def test_postgres_analytics_snapshot_is_unique_and_workspace_scoped():
    assert POSTGRES_URL is not None
    store = PostgresContentStore(POSTGRES_URL)
    analytics = AnalyticsStore(backend="postgres", connect=store.connect)
    try:
        store.init_schema()
        analytics.init_schema()
        now = datetime.now().replace(microsecond=0)
        with store.connect() as db:
            db.execute("TRUNCATE TABLE users CASCADE")
            db.execute(
                "INSERT INTO users(id, telegram_user_id, username, full_name, is_registered, created_at) VALUES (%s, %s, %s, %s, TRUE, %s)",
                (501, 900501, "analytics_owner", "Analytics Owner", now),
            )
            db.execute(
                "INSERT INTO users(id, telegram_user_id, username, full_name, is_registered, created_at) VALUES (%s, %s, %s, %s, TRUE, %s)",
                (502, 900502, "analytics_other", "Analytics Other", now),
            )
            workspace_a = int(db.execute(
                "INSERT INTO workspaces(name, owner_user_id, created_at) VALUES (%s, %s, %s) RETURNING id",
                ("Analytics A", 501, now),
            ).fetchone()["id"])
            workspace_b = int(db.execute(
                "INSERT INTO workspaces(name, owner_user_id, created_at) VALUES (%s, %s, %s) RETURNING id",
                ("Analytics B", 502, now),
            ).fetchone()["id"])
            account_id = int(db.execute(
                "INSERT INTO accounts(owner_user_id, name, platform, destination, options_json, created_at) VALUES (%s, %s, %s, %s, %s::jsonb, %s) RETURNING id",
                (501, "Instagram", "instagram", "@analytics", '{"access_token":"secret"}', now),
            ).fetchone()["id"])
            db.execute(
                "INSERT INTO workspace_accounts(workspace_id, account_id, created_at) VALUES (%s, %s, %s)",
                (workspace_a, account_id, now),
            )

        scoped = store.scoped(workspace_a)
        app = ContentApplication(scoped)
        content = app.create(title="Analytics Master", body="Master body")
        variant = app.upsert_variant(content.id, "instagram", text="IG variant")
        assert variant is not None
        published_at = now - timedelta(hours=2)
        publication = Publication(
            variant_id=variant.id,
            platform="instagram",
            account_id=account_id,
            status=PublicationStatus.PUBLISHED,
            external_post_id="ig-media-501",
            published_at=published_at,
        )
        scoped.save_publication(publication)

        captured = published_at.replace(tzinfo=timezone.utc) + timedelta(hours=1)
        assert analytics.append(
            publication_id=publication.id,
            platform="instagram",
            milestone="1h",
            collector_version="ig-v1",
            metrics={"views": 250, "reach": 180},
            captured_at=captured,
        ) is True
        assert analytics.append(
            publication_id=publication.id,
            platform="instagram",
            milestone="1h",
            collector_version="ig-v1",
            metrics={"views": 999},
            captured_at=captured + timedelta(minutes=1),
        ) is False

        rows_a = analytics.list_for_publication(
            workspace_id=workspace_a,
            publication_id=publication.id,
        )
        rows_b = analytics.list_for_publication(
            workspace_id=workspace_b,
            publication_id=publication.id,
        )
        assert len(rows_a) == 1
        assert rows_a[0].metrics["views"] == 250
        assert rows_b == []

        dataset = analytics.dataset(workspace_id=workspace_a)
        assert len(dataset) == 1
        assert dataset[0]["content_id"] == content.id
        assert dataset[0]["variant_id"] == variant.id
        assert dataset[0]["metrics"]["reach"] == 180
        assert analytics.dataset(workspace_id=workspace_b) == []
    finally:
        store.close()
