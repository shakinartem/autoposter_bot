from __future__ import annotations

from pathlib import Path


def init_workspace_schema(store) -> None:
    """Ensure Content OS tables exist before workspace/security migration.

    This helper is called both after the normal Content OS store bootstrap and from
    migration/tests that start with the legacy Database schema. Making the dependency
    explicit here removes a fragile caller-order requirement. The store schema and SQL
    migration are both restart-safe, so repeated startup remains idempotent.
    """
    init_schema = getattr(store, "init_schema", None)
    if callable(init_schema):
        init_schema()

    migration = Path(__file__).resolve().parents[2] / "migrations" / "003_workspace_security.sql"
    if not migration.exists():
        raise RuntimeError(f"Workspace security migration not found: {migration}")
    with store.connect() as connection:
        connection.executescript(migration.read_text(encoding="utf-8"))
