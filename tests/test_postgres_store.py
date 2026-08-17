from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest

from autoposter_bot.application.content import ContentApplication
from autoposter_bot.domain.content import Publication, PublicationStatus
from autoposter_bot.infrastructure.auth_store import AuthStore
from autoposter_bot.infrastructure.postgres_queue import PostgresPublicationQueue
from autoposter_bot.infrastructure.postgres_store import PostgresContentStore

POSTGRES_URL = os.getenv("AUTOPOSTER_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="PostgreSQL integration URL is not configured")


def test_postgres_workspace_content_atomic_queue_and_retry_schedule():
    assert POSTGRES_URL is not None
    store = PostgresContentStore(POSTGRES_URL)
    try:
        store.init_schema()
        now = datetime.now()
        original_schedule = now - timedelta(seconds=1)
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
            index_row = db.execute(
                "SELECT indexname FROM pg_indexes WHERE indexname = %s",
                ("idx_analytics_snapshots_publication_captured",),
            ).fetchone()
            assert index_row is not None

        scoped = store.scoped(int(workspace_id))
        content = ContentApplication(scoped).create(title="Postgres", body="Content body")
        variant = ContentApplication(scoped).upsert_variant(content.id, "telegram")
        assert variant is not None
        publication = Publication(
            variant_id=variant.id,
            platform="telegram",
            account_id=int(account_id),
            scheduled_at=original_schedule,
            status=PublicationStatus.SCHEDULED,
        )
        scoped.save_publication(publication)

        queue = PostgresPublicationQueue(store)
        assert queue.claim_due(now, limit=10) == [publication.id]
        assert queue.claim_due(now, limit=10) == []

        retry_at = now + timedelta(minutes=10)
        assert queue.schedule_retry(publication.id, retry_at, now=now) is True
        assert queue.claim_due(now + timedelta(minutes=5), limit=10) == []
        assert queue.claim_due(now + timedelta(minutes=11), limit=10) == [publication.id]

        claimed = store.get_publication(publication.id)
        assert claimed is not None
        assert claimed.status == PublicationStatus.QUEUED
        assert claimed.scheduled_at == original_schedule
        with store.connect() as db:
            retry_row = db.execute(
                "SELECT next_attempt_at FROM publications_v2 WHERE id = %s",
                (publication.id,),
            ).fetchone()
        assert retry_row is not None
        assert retry_row["next_attempt_at"] == retry_at
        assert scoped.get_content(content.id) is not None
        assert scoped.get_account(int(account_id)) is not None
    finally:
        store.close()


def test_postgres_auth_sessions_follow_live_workspace_membership():
    assert POSTGRES_URL is not None
    store = PostgresContentStore(POSTGRES_URL)
    auth = AuthStore(backend="postgres", connect=store.connect)
    try:
        store.init_schema()
        auth.init_schema()
        now = datetime.now()
        with store.connect() as db:
            db.execute("TRUNCATE TABLE users CASCADE")
            db.execute(
                "INSERT INTO users(id, telegram_user_id, username, full_name, is_registered, created_at) VALUES (%s, %s, %s, %s, TRUE, %s)",
                (201, 900201, "owner2", "Owner Two", now),
            )
            db.execute(
                "INSERT INTO users(id, telegram_user_id, username, full_name, is_registered, created_at) VALUES (%s, %s, %s, %s, TRUE, %s)",
                (202, 900202, "member2", "Member Two", now),
            )
            workspace_id = int(
                db.execute(
                    "INSERT INTO workspaces(name, owner_user_id, created_at) VALUES (%s, %s, %s) RETURNING id",
                    ("Auth Production", 201, now),
                ).fetchone()["id"]
            )

        auth.init_schema()
        owner = auth.get_membership(workspace_id, 201)
        assert owner is not None and owner["role"] == "owner"
        auth.set_membership(workspace_id, 202, "viewer")

        token, issued = auth.create_session(
            user_id=202,
            workspace_id=workspace_id,
            ttl_seconds=3600,
        )
        resolved = auth.resolve_session(token)
        assert resolved is not None
        assert resolved.session_id == issued.session_id
        assert resolved.role == "viewer"

        with store.connect() as db:
            row = db.execute(
                "SELECT token_hash FROM user_sessions WHERE id = %s",
                (issued.session_id,),
            ).fetchone()
        assert row is not None
        assert row["token_hash"] == auth.hash_token(token)
        assert token not in row["token_hash"]

        auth.set_membership(workspace_id, 202, "editor")
        promoted = auth.resolve_session(token)
        assert promoted is not None and promoted.role == "editor"

        assert auth.revoke_session(token) is True
        assert auth.resolve_session(token) is None
    finally:
        store.close()
