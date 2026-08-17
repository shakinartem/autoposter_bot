from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


class SQLitePublicationQueue:
    """Atomic queue operations for scheduled Content OS publications.

    SQLite serializes the short claim transaction, so multiple worker processes
    cannot claim the same publication. PostgreSQL can later replace this with
    SELECT ... FOR UPDATE SKIP LOCKED without changing worker behavior.
    """

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
                  AND scheduled_at <= ?
                ORDER BY scheduled_at, created_at
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
                """,
                (now.isoformat(), cutoff),
            )
            connection.commit()
            return int(cursor.rowcount)
        finally:
            connection.close()
