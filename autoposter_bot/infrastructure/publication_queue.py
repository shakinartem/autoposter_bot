from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


class SQLitePublicationQueue:
    """Atomic queue operations for scheduled Content OS publications."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def claim_due(self, now: datetime, *, limit: int = 25) -> list[str]:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
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
        connection = sqlite3.connect(self.db_path, timeout=30)
        try:
            cursor = connection.execute(
                """
                UPDATE publications_v2
                SET status = 'scheduled', next_attempt_at = ?, updated_at = ?
                WHERE id = ?
                  AND status IN ('failed', 'queued', 'publishing')
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
        connection = sqlite3.connect(self.db_path, timeout=30)
        try:
            connection.execute(
                "UPDATE publications_v2 SET next_attempt_at = NULL, updated_at = ? WHERE id = ?",
                (now.isoformat(), publication_id),
            )
            connection.commit()
        finally:
            connection.close()

    def requeue_stale(self, now: datetime, *, stale_after: timedelta = timedelta(minutes=15)) -> int:
        cutoff = (now - stale_after).isoformat()
        connection = sqlite3.connect(self.db_path, timeout=30)
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
