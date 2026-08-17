from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from autoposter_bot.config import Settings
from autoposter_bot.infrastructure.content_store import SQLiteContentStore
from autoposter_bot.infrastructure.credentials import CredentialCipher, SecureContentStore
from autoposter_bot.infrastructure.postgres_queue import PostgresPublicationQueue
from autoposter_bot.infrastructure.postgres_store import PostgresContentStore
from autoposter_bot.infrastructure.publication_queue import SQLitePublicationQueue
from autoposter_bot.infrastructure.workspace_schema import init_workspace_schema
from autoposter_bot.infrastructure.workspace_store import WorkspaceContentStore


@dataclass(slots=True)
class PersistenceRuntime:
    backend: str
    store: SecureContentStore
    queue: Any

    def init_schema(self) -> None:
        self.store.init_schema()
        if self.backend == "sqlite":
            init_workspace_schema(self.store.base)

    def scoped(self, workspace_id: int) -> SecureContentStore:
        if self.backend == "postgres":
            return self.store.scoped(workspace_id)
        return SecureContentStore(
            WorkspaceContentStore(self.store.base, workspace_id),
            backend="sqlite",
            cipher=self.store.cipher,
        )

    def close(self) -> None:
        close = getattr(self.store, "close", None)
        if callable(close):
            close()


def _truthy_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def build_persistence(settings: Settings) -> PersistenceRuntime:
    database_url = os.getenv("AUTOPOSTER_DATABASE_URL", "").strip()
    if database_url:
        if not database_url.startswith(("postgresql://", "postgres://")):
            raise RuntimeError("AUTOPOSTER_DATABASE_URL currently supports PostgreSQL URLs only")
        raw_store = PostgresContentStore(database_url)
        cipher = CredentialCipher.from_env(required=True)
        return PersistenceRuntime(
            backend="postgres",
            store=SecureContentStore(raw_store, backend="postgres", cipher=cipher),
            queue=PostgresPublicationQueue(raw_store),
        )

    raw_store = SQLiteContentStore(settings.database_path)
    cipher = CredentialCipher.from_env(
        required=_truthy_env("AUTOPOSTER_REQUIRE_CREDENTIAL_ENCRYPTION", default=False)
    )
    return PersistenceRuntime(
        backend="sqlite",
        store=SecureContentStore(raw_store, backend="sqlite", cipher=cipher),
        queue=SQLitePublicationQueue(settings.database_path),
    )
