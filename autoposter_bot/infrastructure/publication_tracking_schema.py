from __future__ import annotations

from typing import Any, Callable, ContextManager


def init_publication_tracking_schema(
    *,
    backend: str,
    connect: Callable[[], ContextManager[Any]],
) -> None:
    """Add provider tracking identity without forcing a destructive migration."""
    if backend not in {"sqlite", "postgres"}:
        raise ValueError(f"Unsupported tracking schema backend: {backend}")

    with connect() as connection:
        if backend == "postgres":
            exists = connection.execute(
                "SELECT to_regclass('public.publications_v2') AS table_name"
            ).fetchone()
            if not exists or exists["table_name"] is None:
                return
            connection.execute(
                "ALTER TABLE publications_v2 ADD COLUMN IF NOT EXISTS provider_tracking_id TEXT"
            )
            connection.execute(
                "ALTER TABLE publication_attempts ADD COLUMN IF NOT EXISTS provider_tracking_id TEXT"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_publications_v2_tracking ON publications_v2(platform, provider_tracking_id)"
            )
            return

        publication_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(publications_v2)").fetchall()
        }
        if not publication_columns:
            return
        if "provider_tracking_id" not in publication_columns:
            connection.execute("ALTER TABLE publications_v2 ADD COLUMN provider_tracking_id TEXT")
        attempt_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(publication_attempts)").fetchall()
        }
        if attempt_columns and "provider_tracking_id" not in attempt_columns:
            connection.execute("ALTER TABLE publication_attempts ADD COLUMN provider_tracking_id TEXT")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_publications_v2_tracking ON publications_v2(platform, provider_tracking_id)"
        )
