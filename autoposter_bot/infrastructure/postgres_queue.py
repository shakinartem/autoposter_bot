from __future__ import annotations

from datetime import datetime, timedelta

from autoposter_bot.infrastructure.postgres_store import PostgresContentStore
from autoposter_bot.infrastructure.retry_schema import init_retry_schema


class PostgresPublicationQueue:
    """Horizontal-worker-safe queue using row locks and SKIP LOCKED."""

    def __init__(self, store: PostgresContentStore) -> None:
        self.store = store
        init_retry_schema(backend="postgres", connect=store.connect)

    def claim_due(self, now: datetime, *, limit: int = 25) -> list[str]:
        with self.store.connect() as connection:
            rows = connection.execute(
                """
                WITH due AS (
                    SELECT id
                    FROM publications_v2
                    WHERE status = 'scheduled'
                      AND scheduled_at IS NOT NULL
                      AND COALESCE(next_attempt_at, scheduled_at) <= %s
                    ORDER BY COALESCE(next_attempt_at, scheduled_at), created_at
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

    def schedule_retry(self, publication_id: str, next_attempt_at: datetime, *, now: datetime) -> bool:
        with self.store.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE publications_v2
                SET status = 'scheduled', next_attempt_at = %s, updated_at = %s
                WHERE id = %s
                  AND status IN ('failed', 'queued', 'publishing')
                  AND published_at IS NULL
                  AND external_post_id IS NULL
                """,
                (next_attempt_at, now, publication_id),
            )
            return bool(cursor.rowcount)

    def clear_retry(self, publication_id: str, *, now: datetime) -> None:
        with self.store.connect() as connection:
            connection.execute(
                "UPDATE publications_v2 SET next_attempt_at = NULL, updated_at = %s WHERE id = %s",
                (now, publication_id),
            )

    def requeue_stale(self, now: datetime, *, stale_after: timedelta = timedelta(minutes=15)) -> int:
        cutoff = now - stale_after
        with self.store.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE publications_v2
                SET status = 'scheduled', updated_at = %s
                WHERE status = 'queued'
                  AND updated_at < %s
                  AND published_at IS NULL
                  AND external_post_id IS NULL
                """,
                (now, cutoff),
            )
            return int(cursor.rowcount)
