from __future__ import annotations

import os
from datetime import datetime

import pytest
from pydantic import ValidationError

from autoposter_bot.application.content_factory_bridge import ContentFactoryBridge, IdempotencyConflict
from autoposter_bot.application.content_factory_feedback import ContentFactoryFeedback
from autoposter_bot.apps.api.content_factory_contract import ContentPackageV1, package_hash
from autoposter_bot.domain.content import Publication
from autoposter_bot.infrastructure.content_factory_ledger import ContentFactoryLedger
from autoposter_bot.infrastructure.credentials import SecureContentStore
from autoposter_bot.infrastructure.media_storage import LocalMediaStorage
from autoposter_bot.infrastructure.persistence import PersistenceRuntime
from autoposter_bot.infrastructure.postgres_queue import PostgresPublicationQueue
from autoposter_bot.infrastructure.postgres_store import PostgresContentStore
from autoposter_bot.infrastructure.publication_queue import SQLitePublicationQueue
from autoposter_bot.infrastructure.content_store import SQLiteContentStore

POSTGRES_URL = os.getenv("AUTOPOSTER_TEST_POSTGRES_URL")


def package_fixture(**canonical_overrides):
    canonical = {
        "content_type": "post",
        "topic": "Intent signals",
        "goal": "Educate",
        "title": "How intent becomes action",
        "body": "Signals only matter when they improve a future decision.",
        "hook": "Not every lead is ready now.",
        "cta": "Review the signal before acting.",
    }
    canonical.update(canonical_overrides)
    return ContentPackageV1.model_validate({
        "schema_version": "content-package/1.0",
        "content_id": "11111111-1111-1111-1111-111111111111",
        "project_id": "22222222-2222-2222-2222-222222222222",
        "status": "approved",
        "canonical": canonical,
        "variants": [{
            "platform": "telegram",
            "title": canonical["title"],
            "plain_text": canonical["body"],
            "cta": canonical["cta"],
            "hashtags": ["#intent"],
            "blocks": [],
            "media": [],
        }],
        "sources": [],
        "quality": {"overall": 0.94, "factuality": 0.98, "brand_voice": 0.91, "media": None},
    })


def _sqlite_runtime(tmp_path):
    db_path = tmp_path / "autoposter.sqlite3"
    raw = SQLiteContentStore(db_path)
    runtime = PersistenceRuntime(
        backend="sqlite",
        store=SecureContentStore(raw, backend="sqlite", cipher=None),
        queue=SQLitePublicationQueue(db_path),
    )
    runtime.init_schema()
    with raw.connect() as db:
        now = datetime.now().isoformat()
        db.execute("INSERT INTO users(id, telegram_user_id, username, full_name, is_registered, created_at) VALUES (?, ?, ?, ?, 1, ?)", (101, 900101, "owner", "Owner", now))
        workspace_id = int(db.execute("INSERT INTO workspaces(name, owner_user_id, created_at) VALUES (?, ?, ?)", ("Production", 101, now)).lastrowid)
        account_id = int(db.execute("INSERT INTO accounts(owner_user_id, name, platform, destination, options_json, created_at) VALUES (?, ?, ?, ?, '{}', ?)", (101, "Telegram", "telegram", "@channel", now)).lastrowid)
    return runtime, workspace_id, account_id


def _bridge(runtime, workspace_id, tmp_path):
    ledger = ContentFactoryLedger(runtime)
    ledger.init_schema()
    media = LocalMediaStorage(tmp_path / "media")
    bridge = ContentFactoryBridge(
        runtime,
        ledger,
        media_ingestor=__import__("autoposter_bot.infrastructure.content_factory_media", fromlist=["DurableMediaIngestor"]).DurableMediaIngestor(media, ledger),
        default_workspace_id=workspace_id,
    )
    return bridge, ledger


def test_contract_is_strict_and_hash_is_stable():
    package = package_fixture()
    assert package_hash(package) == package_hash(package_fixture())
    payload = package.model_dump(mode="json")
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        ContentPackageV1.model_validate(payload)


def test_sqlite_ingest_idempotency_workspace_and_feedback(monkeypatch, tmp_path):
    runtime, workspace_id, account_id = _sqlite_runtime(tmp_path)
    bridge, ledger = _bridge(runtime, workspace_id, tmp_path)
    package = package_fixture()

    first = bridge.ingest(package, idempotency_key="delivery-1", supported_platforms={"telegram"})
    replay = bridge.ingest(package, idempotency_key="delivery-1", supported_platforms={"telegram"})
    assert first["workspace_id"] == workspace_id
    assert replay["idempotent_replay"] is True
    assert replay["receipt_id"] == first["receipt_id"]
    assert len(runtime.scoped(workspace_id).list_content()) == 1

    with pytest.raises(IdempotencyConflict):
        bridge.ingest(package_fixture(body="Changed payload"), idempotency_key="delivery-1", supported_platforms={"telegram"})

    publication = Publication(
        variant_id=first["variants"][0]["id"],
        platform="telegram",
        account_id=account_id,
        external_post_id="telegram-post-123",
        external_url="https://t.me/channel/123",
    )
    runtime.scoped(workspace_id).save_publication(publication)
    feedback = ContentFactoryFeedback(ledger, endpoint="http://factory/performance/ingest", token="secret")
    snapshot = feedback.record_snapshot(publication.id, event_id="evt-1", metrics={"views": 100, "clicks": 4, "leads": 1})
    duplicate = feedback.record_snapshot(publication.id, event_id="evt-1", metrics={"views": 100})
    assert snapshot["status"] == "accepted"
    assert duplicate["status"] == "duplicate"

    calls = []

    class Response:
        status_code = 202
        text = "accepted"

    def fake_post(url, *, json, headers, timeout):
        calls.append((url, json, headers, timeout))
        return Response()

    monkeypatch.setattr("autoposter_bot.application.content_factory_feedback.requests.post", fake_post)
    assert feedback.dispatch() == {"sent": 1, "failed": 0, "disabled": 0}
    assert calls[0][1]["content_id"] == package.content_id
    assert calls[0][1]["external_publication_id"] == "telegram-post-123"
    assert calls[0][2]["X-Performance-Token"] == "secret"
    runtime.close()


@pytest.mark.skipif(not POSTGRES_URL, reason="PostgreSQL integration URL is not configured")
def test_postgres_bridge_uses_mapped_workspace(tmp_path):
    assert POSTGRES_URL is not None
    raw = PostgresContentStore(POSTGRES_URL)
    runtime = PersistenceRuntime(
        backend="postgres",
        store=SecureContentStore(raw, backend="postgres", cipher=None),
        queue=PostgresPublicationQueue(raw),
    )
    try:
        runtime.init_schema()
        now = datetime.now()
        with raw.connect() as db:
            db.execute("TRUNCATE TABLE users CASCADE")
            db.execute("INSERT INTO users(id, telegram_user_id, username, full_name, is_registered, created_at) VALUES (%s, %s, %s, %s, TRUE, %s)", (101, 900101, "owner", "Owner", now))
            workspace_id = int(db.execute("INSERT INTO workspaces(name, owner_user_id, created_at) VALUES (%s, %s, %s) RETURNING id", ("Production", 101, now)).fetchone()["id"])

        bridge, ledger = _bridge(runtime, workspace_id, tmp_path)
        package = package_fixture()
        result = bridge.ingest(package, idempotency_key="pg-delivery-1", supported_platforms={"telegram"})
        assert result["workspace_id"] == workspace_id
        assert runtime.scoped(workspace_id).get_content(result["content_id"]) is not None
        receipt = ledger.get_receipt("pg-delivery-1")
        assert receipt is not None
        assert int(receipt["workspace_id"]) == workspace_id
    finally:
        runtime.close()
