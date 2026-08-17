from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest

from autoposter_bot.application.content import ContentApplication
from autoposter_bot.domain.content import Publication, PublicationStatus
from autoposter_bot.infrastructure.postgres_queue import PostgresPublicationQueue
from autoposter_bot.infrastructure.postgres_store import PostgresContentStore

POSTGRES_URL = os.getenv("AUTOPOSTER_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="PostgreSQL integration URL is not configured")


def test_postgres_workspace_content_and_atomic_queue():
    assert POSTGRES_URL is not None
    store = PostgresContentStore(POSTGRES_URL)
    try:
        store.init_schema()
        now = datetime.now()
        with store.connect() as db:
            db.execute("TRUNCATE TABLE users CASCADE")
            db.execute(
                "INSERT INTO users(id, telegram_user_id, username, full_name, is_registered, created_at) VALUES (%s, %s, %s, %s, TRUE, %s)",
                (101, 900101, "owner", "Owner", now),
            )
            workspace_id = db.execute(
                "INSERT INTO workspaces(name, owner_user_id, created_at) VALUES (%s, %s, %s) RETURNING id",
                ("Production", 101, now),
            ).fetchone()["id"]
            account_id = db.execute(
                "INSERT INTO accounts(owner_user_id, name, platform, destination, options_json, created_at) VALUES (%s, %s, %s, %s, %s::jsonb, %s) RETURNING id",
                (101, "Telegram", "telegram", "-100123", '{"access_token":"secret"}', now),
            ).fetchone()["id"]

        scoped = store.scoped(int(workspace_id))
        content = ContentApplication(scoped).create(title="Postgres", body="Content body")
        variant = ContentApplication(scoped).upsert_variant(content.id, "telegram")
        assert variant is not None
        publication = Publication(
            variant_id=variant.id,
            platform="telegram",
            account_id=int(account_id),
            scheduled_at=now - timedelta(seconds=1),
            status=PublicationStatus.SCHEDULED,
        )
        scoped.save_publication(publication)

        queue = PostgresPublicationQueue(store)
        assert queue.claim_due(now, limit=10) == [publication.id]
        assert queue.claim_due(now, limit=10) == []
        claimed = store.get_publication(publication.id)
        assert claimed is not None
        assert claimed.status == PublicationStatus.QUEUED
        assert scoped.get_content(content.id) is not None
        assert scoped.get_account(int(account_id)) is not None
    finally:
        store.close()
