from __future__ import annotations

from datetime import datetime, timedelta

from autoposter_bot.infrastructure.postgres_store import PostgresContentStore


class PostgresPublicationQueue:
    """Horizontal-worker-safe queue using row locks and SKIP LOCKED."""

    def __init__(self, store: PostgresContentStore) -> None:
        self.store = store

    def claim_due(self, now: datetime, *, limit: int = 25) -> list[str]:
        with self.store.connect() as connection:
            rows = connection.execute(
                """
                WITH due AS (
                    SELECT id
                    FROM publications_v2
                    WHERE status = 'scheduled'
                      AND scheduled_at IS NOT NULL
                      AND scheduled_at <= %s
                    ORDER BY scheduled_at, created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s
                )
                UPDATE publications_v2 AS p
                SET status = 'queued', updated_at = %s
                FROM due
                WHERE p.id = due.id
                RETURNING p.id
                """,
                (now, limit, now),
            ).fetchall()
        return [str(row["id"]) for row in rows]

    def requeue_stale(self, now: datetime, *, stale_after: timedelta = timedelta(minutes=15)) -> int:
        cutoff = now - stale_after
        with self.store.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE publications_v2
                SET status = 'scheduled', updated_at = %s
                WHERE status = 'queued'
                  AND updated_at < %s
                """,
                (now, cutoff),
            )
            return int(cursor.rowcount)
