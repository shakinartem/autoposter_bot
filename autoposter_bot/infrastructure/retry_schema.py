from __future__ import annotations

from typing import Any, Callable, ContextManager


def init_retry_schema(
    *,
    backend: str,
    connect: Callable[[], ContextManager[Any]],
) -> None:
    """Install retry scheduling storage without rewriting old migrations.

    Content OS currently stores publication scheduling timestamps without a
    timezone. `next_attempt_at` intentionally follows that same contract so the
    queue never compares timezone-aware and timezone-naive values. Existing
    early retry columns created as TIMESTAMPTZ are normalized to UTC first.
    """

    if backend not in {"sqlite", "postgres"}:
        raise ValueError(f"Unsupported retry schema backend: {backend}")

    with connect() as connection:
        if backend == "postgres":
            exists = connection.execute(
                "SELECT to_regclass('public.publications_v2') AS table_name"
            ).fetchone()
            if not exists or exists["table_name"] is None:
                return

            connection.execute(
                "ALTER TABLE publications_v2 ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMP"
            )
            column = connection.execute(
                """
                SELECT data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'publications_v2'
                  AND column_name = 'next_attempt_at'
                """
            ).fetchone()
            if column and column["data_type"] == "timestamp with time zone":
                connection.execute(
                    """
                    ALTER TABLE publications_v2
                    ALTER COLUMN next_attempt_at TYPE TIMESTAMP
                    USING next_attempt_at AT TIME ZONE 'UTC'
                    """
                )

            connection.execute("DROP INDEX IF EXISTS idx_publications_v2_due")
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_publications_v2_due
                ON publications_v2(status, next_attempt_at, scheduled_at)
                """
            )
            return

        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(publications_v2)").fetchall()
        }
        if not columns:
            return
        if "next_attempt_at" not in columns:
            connection.execute("ALTER TABLE publications_v2 ADD COLUMN next_attempt_at TEXT")
        connection.execute("DROP INDEX IF EXISTS idx_publications_v2_due")
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_publications_v2_due
            ON publications_v2(status, next_attempt_at, scheduled_at)
            """
        )
