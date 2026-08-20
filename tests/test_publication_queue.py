from datetime import datetime, timedelta

from autoposter_bot.application.content import ContentApplication
from autoposter_bot.domain.content import Publication, PublicationStatus
from autoposter_bot.infrastructure.content_store import SQLiteContentStore
from autoposter_bot.infrastructure.publication_queue import SQLitePublicationQueue


def test_due_publication_is_claimed_only_once(tmp_path):
    db_path = tmp_path / "autoposter.sqlite3"
    store = SQLiteContentStore(db_path)
    store.init_schema()
    app = ContentApplication(store)

    master = app.create(title="Scheduled", body="Hello")
    variant = app.upsert_variant(master.id, "telegram")
    assert variant is not None

    now = datetime(2026, 8, 17, 12, 0, 0)
    with store.connect() as connection:
        connection.execute(
            """
            INSERT INTO accounts(name, platform, destination, options_json, created_at)
            VALUES (?, ?, ?, '{}', ?)
            """,
            ("test-telegram", "telegram", "@example", now.isoformat()),
        )
        account_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])

    publication = Publication(
        variant_id=variant.id,
        platform="telegram",
        account_id=account_id,
        destination="@example",
        scheduled_at=now - timedelta(minutes=1),
        status=PublicationStatus.SCHEDULED,
    )
    store.save_publication(publication)

    queue = SQLitePublicationQueue(db_path)
    first_claim = queue.claim_due(now)
    second_claim = queue.claim_due(now)

    assert first_claim == [publication.id]
    assert second_claim == []
    claimed = store.get_publication(publication.id)
    assert claimed is not None
    assert claimed.status == PublicationStatus.QUEUED
