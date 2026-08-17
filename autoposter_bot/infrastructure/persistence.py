from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from autoposter_bot.config import Settings
from autoposter_bot.infrastructure.content_store import SQLiteContentStore
from autoposter_bot.infrastructure.postgres_queue import PostgresPublicationQueue
from autoposter_bot.infrastructure.postgres_store import PostgresContentStore
from autoposter_bot.infrastructure.publication_queue import SQLitePublicationQueue
from autoposter_bot.infrastructure.workspace_schema import init_workspace_schema
from autoposter_bot.infrastructure.workspace_store import WorkspaceContentStore


@dataclass(slots=True)
class PersistenceRuntime:
    backend: str
    store: Any
    queue: Any

    def init_schema(self) -> None:
        self.store.init_schema()
        if self.backend == "sqlite":
            init_workspace_schema(self.store)

    def scoped(self, workspace_id: int) -> Any:
        if self.backend == "postgres":
            return self.store.scoped(workspace_id)
        return WorkspaceContentStore(self.store, workspace_id)

    def close(self) -> None:
        close = getattr(self.store, "close", None)
        if callable(close):
            close()


def build_persistence(settings: Settings) -> PersistenceRuntime:
    database_url = os.getenv("AUTOPOSTER_DATABASE_URL", "").strip()
    if database_url:
        if not database_url.startswith(("postgresql://", "postgres://")):
            raise RuntimeError("AUTOPOSTER_DATABASE_URL currently supports PostgreSQL URLs only")
        store = PostgresContentStore(database_url)
        return PersistenceRuntime(
            backend="postgres",
            store=store,
            queue=PostgresPublicationQueue(store),
        )

    store = SQLiteContentStore(settings.database_path)
    return PersistenceRuntime(
        backend="sqlite",
        store=store,
        queue=SQLitePublicationQueue(settings.database_path),
    )
