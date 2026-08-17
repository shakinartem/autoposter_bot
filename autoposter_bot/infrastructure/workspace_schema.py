from pathlib import Path


def init_workspace_schema(store) -> None:
    migration = Path(__file__).resolve().parents[2] / "migrations" / "003_workspace_security.sql"
    if not migration.exists():
        raise RuntimeError(f"Workspace security migration not found: {migration}")
    with store.connect() as connection:
        connection.executescript(migration.read_text(encoding="utf-8"))
