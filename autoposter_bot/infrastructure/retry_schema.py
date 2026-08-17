from __future__ import annotations

from typing import Any, Callable, ContextManager


def init_retry_schema(
    *,
    backend: str,
    connect: Callable[[], ContextManager[Any]],
) -> None:
    """Install retry scheduling storage without rewriting old migrations.

    SQLite cannot add a column with IF NOT EXISTS, so we inspect table metadata.
    PostgreSQL can apply the migration idempotently on every process start.
    If the Content OS tables have not been created yet, this helper is a no-op;
    the normal persistence schema bootstrap will call it again afterwards.
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
                "ALTER TABLE publications_v2 ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ"
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
