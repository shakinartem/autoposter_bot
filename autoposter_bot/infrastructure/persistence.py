from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from autoposter_bot.config import Settings
from autoposter_bot.infrastructure.analytics_store import AnalyticsStore
from autoposter_bot.infrastructure.auth_store import AuthStore
from autoposter_bot.infrastructure.content_store import SQLiteContentStore
from autoposter_bot.infrastructure.credentials import CredentialCipher, SecureContentStore
from autoposter_bot.infrastructure.identity_store import IdentityStore
from autoposter_bot.infrastructure.invitation_store import InvitationStore
from autoposter_bot.infrastructure.login_grant_store import LoginGrantStore
from autoposter_bot.infrastructure.operations_store import OperationsStore
from autoposter_bot.infrastructure.postgres_queue import PostgresPublicationQueue
from autoposter_bot.infrastructure.postgres_store import PostgresContentStore
from autoposter_bot.infrastructure.publication_queue import SQLitePublicationQueue
from autoposter_bot.infrastructure.publication_tracking_schema import init_publication_tracking_schema
from autoposter_bot.infrastructure.retry_schema import init_retry_schema
from autoposter_bot.infrastructure.workspace_schema import init_workspace_schema
from autoposter_bot.infrastructure.workspace_store import WorkspaceContentStore


@dataclass(slots=True)
class PersistenceRuntime:
    backend: str
    store: SecureContentStore
    queue: Any
    auth: AuthStore
    identities: IdentityStore
    login_grants: LoginGrantStore
    invitations: InvitationStore
    analytics: AnalyticsStore
    operations: OperationsStore

    def init_schema(self) -> None:
        self.store.init_schema()
        if self.backend == "sqlite":
            init_workspace_schema(self.store.base)
        init_retry_schema(backend=self.backend, connect=self.store.base.connect)
        init_publication_tracking_schema(backend=self.backend, connect=self.store.base.connect)
        self.auth.init_schema()
        self.identities.init_schema()
        self.login_grants.init_schema()
        self.invitations.init_schema()
        self.analytics.init_schema()

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


def _runtime(
    *,
    backend: str,
    raw_store: Any,
    secure_store: SecureContentStore,
    queue: Any,
) -> PersistenceRuntime:
    connect = raw_store.connect
    return PersistenceRuntime(
        backend=backend,
        store=secure_store,
        queue=queue,
        auth=AuthStore(backend=backend, connect=connect),
        identities=IdentityStore(backend=backend, connect=connect),
        login_grants=LoginGrantStore(
            backend=backend,
            connect=connect,
            ttl_seconds=int(os.getenv("AUTOPOSTER_LOGIN_GRANT_TTL_SECONDS", "120")),
        ),
        invitations=InvitationStore(backend=backend, connect=connect),
        analytics=AnalyticsStore(backend=backend, connect=connect),
        operations=OperationsStore(backend=backend, connect=connect),
    )


def build_persistence(settings: Settings) -> PersistenceRuntime:
    database_url = os.getenv("AUTOPOSTER_DATABASE_URL", "").strip()
    if database_url:
        if not database_url.startswith(("postgresql://", "postgres://")):
            raise RuntimeError("AUTOPOSTER_DATABASE_URL currently supports PostgreSQL URLs only")
        raw_store = PostgresContentStore(database_url)
        cipher = CredentialCipher.from_env(required=True)
        secure_store = SecureContentStore(raw_store, backend="postgres", cipher=cipher)
        return _runtime(
            backend="postgres",
            raw_store=raw_store,
            secure_store=secure_store,
            queue=PostgresPublicationQueue(raw_store),
        )

    raw_store = SQLiteContentStore(settings.database_path)
    cipher = CredentialCipher.from_env(
        required=_truthy_env("AUTOPOSTER_REQUIRE_CREDENTIAL_ENCRYPTION", default=False)
    )
    secure_store = SecureContentStore(raw_store, backend="sqlite", cipher=cipher)
    return _runtime(
        backend="sqlite",
        raw_store=raw_store,
        secure_store=secure_store,
        queue=SQLitePublicationQueue(settings.database_path),
    )
