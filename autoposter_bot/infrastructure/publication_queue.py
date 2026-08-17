from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from autoposter_bot.infrastructure.retry_schema import init_retry_schema


class SQLitePublicationQueue:
    """Atomic queue operations for scheduled Content OS publications."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        init_retry_schema(backend="sqlite", connect=self._connect)

    def _connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def claim_due(self, now: datetime, *, limit: int = 25) -> list[str]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT id
                FROM publications_v2
                WHERE status = 'scheduled'
                  AND scheduled_at IS NOT NULL
                  AND COALESCE(next_attempt_at, scheduled_at) <= ?
                ORDER BY COALESCE(next_attempt_at, scheduled_at), created_at
                LIMIT ?
                """,
                (now.isoformat(), limit),
            ).fetchall()
            ids = [str(row["id"]) for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                connection.execute(
                    f"""
                    UPDATE publications_v2
                    SET status = 'queued', updated_at = ?
                    WHERE status = 'scheduled'
                      AND id IN ({placeholders})
                    """,
                    (now.isoformat(), *ids),
                )
            connection.commit()
            return ids
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def schedule_retry(self, publication_id: str, next_attempt_at: datetime, *, now: datetime) -> bool:
        connection = self._connect()
        try:
            cursor = connection.execute(
                """
                UPDATE publications_v2
                SET status = 'scheduled', next_attempt_at = ?, updated_at = ?
                WHERE id = ?
                  AND status IN ('failed', 'queued')
                  AND published_at IS NULL
                  AND external_post_id IS NULL
                """,
                (next_attempt_at.isoformat(), now.isoformat(), publication_id),
            )
            connection.commit()
            return bool(cursor.rowcount)
        finally:
            connection.close()

    def clear_retry(self, publication_id: str, *, now: datetime) -> None:
        connection = self._connect()
        try:
            connection.execute(
                "UPDATE publications_v2 SET next_attempt_at = NULL, updated_at = ? WHERE id = ?",
                (now.isoformat(), publication_id),
            )
            connection.commit()
        finally:
            connection.close()

    def quarantine_stale_publishing(
        self,
        now: datetime,
        *,
        stale_after: timedelta = timedelta(hours=2),
    ) -> int:
        cutoff = (now - stale_after).isoformat()
        connection = self._connect()
        try:
            cursor = connection.execute(
                """
                UPDATE publications_v2
                SET status = 'failed',
                    next_attempt_at = NULL,
                    last_error_code = 'unknown_publish_outcome',
                    last_error_message = 'Worker stopped after remote publish started; reconcile the platform before retrying',
                    updated_at = ?
                WHERE status = 'publishing'
                  AND updated_at < ?
                  AND published_at IS NULL
                  AND external_post_id IS NULL
                """,
                (now.isoformat(), cutoff),
            )
            connection.commit()
            return int(cursor.rowcount)
        finally:
            connection.close()

    def requeue_stale(self, now: datetime, *, stale_after: timedelta = timedelta(minutes=15)) -> int:
        cutoff = (now - stale_after).isoformat()
        connection = self._connect()
        try:
            cursor = connection.execute(
                """
                UPDATE publications_v2
                SET status = 'scheduled', updated_at = ?
                WHERE status = 'queued'
                  AND updated_at < ?
                  AND published_at IS NULL
                  AND external_post_id IS NULL
                """,
                (now.isoformat(), cutoff),
            )
            connection.commit()
            return int(cursor.rowcount)
        finally:
            connection.close()
