from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, Callable, ContextManager


class IdentityStore:
    """External identity -> internal user mapping.

    Telegram is the first public identity provider, but the schema intentionally
    uses `(provider, subject)` so Google/OIDC/enterprise SSO can be added without
    changing workspace memberships or browser sessions.
    """

    def __init__(
        self,
        *,
        backend: str,
        connect: Callable[[], ContextManager[Any]],
    ) -> None:
        if backend not in {"sqlite", "postgres"}:
            raise ValueError(f"Unsupported identity backend: {backend}")
        self.backend = backend
        self.connect = connect

    def init_schema(self) -> None:
        with self.connect() as connection:
            if self.backend == "postgres":
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_identities (
                        provider TEXT NOT NULL,
                        subject TEXT NOT NULL,
                        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        claims_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL,
                        PRIMARY KEY(provider, subject)
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_user_identities_user ON user_identities(user_id, provider)"
                )
            else:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_identities (
                        provider TEXT NOT NULL,
                        subject TEXT NOT NULL,
                        user_id INTEGER NOT NULL,
                        claims_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY(provider, subject),
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_user_identities_user ON user_identities(user_id, provider)"
                )

    def find_or_create_telegram_user(self, claims: dict[str, Any]) -> dict[str, Any]:
        subject = str(claims.get("sub") or "").strip()
        telegram_id_raw = claims.get("id")
        if not subject or telegram_id_raw in (None, ""):
            raise ValueError("Telegram ID token is missing sub/id claims")
        telegram_id = int(telegram_id_raw)
        if telegram_id <= 0:
            raise ValueError("Telegram user id is invalid")

        existing = self._identity_user("telegram", subject)
        if existing is None:
            existing = self._telegram_user(telegram_id)

        username = str(claims.get("preferred_username") or "").strip() or None
        full_name = str(claims.get("name") or "").strip() or username or f"Telegram {telegram_id}"
        phone = str(claims.get("phone_number") or "").strip() or None
        now = datetime.now(timezone.utc)

        if existing is None:
            user_id = self._insert_user(
                telegram_id=telegram_id,
                username=username,
                full_name=full_name,
                phone=phone,
                now=now,
            )
        else:
            user_id = int(existing["id"])
            self._update_user_profile(
                user_id=user_id,
                telegram_id=telegram_id,
                username=username,
                full_name=full_name,
                phone=phone,
                now=now,
            )

        self._upsert_identity(
            provider="telegram",
            subject=subject,
            user_id=user_id,
            claims=claims,
            now=now,
        )
        user = self._user_by_id(user_id)
        assert user is not None
        return user

    def primary_workspace(self, user_id: int) -> dict[str, Any] | None:
        placeholder = "%s" if self.backend == "postgres" else "?"
        with self.connect() as connection:
            row = connection.execute(
                f"""
                SELECT w.id, w.name, w.owner_user_id, wm.role, w.created_at
                FROM workspace_members wm
                JOIN workspaces w ON w.id = wm.workspace_id
                WHERE wm.user_id = {placeholder}
                ORDER BY CASE WHEN w.owner_user_id = {placeholder} THEN 0
                              WHEN wm.role = 'admin' THEN 1
                              WHEN wm.role IN ('editor', 'member') THEN 2
                              ELSE 3 END,
                         w.created_at,
                         w.id
                LIMIT 1
                """,
                (user_id, user_id),
            ).fetchone()
        return dict(row) if row else None

    def ensure_personal_workspace(self, user_id: int, display_name: str) -> dict[str, Any]:
        existing = self.primary_workspace(user_id)
        if existing is not None:
            return existing
        now = datetime.now(timezone.utc)
        name = f"{display_name.strip() or 'Autoposter'} Workspace"[:160]
        with self.connect() as connection:
            if self.backend == "postgres":
                workspace_id = int(
                    connection.execute(
                        "INSERT INTO workspaces(name, owner_user_id, created_at) VALUES (%s, %s, %s) RETURNING id",
                        (name, user_id, now),
                    ).fetchone()["id"]
                )
                connection.execute(
                    "INSERT INTO workspace_members(workspace_id, user_id, role, created_at) VALUES (%s, %s, 'owner', %s)",
                    (workspace_id, user_id, now),
                )
            else:
                cursor = connection.execute(
                    "INSERT INTO workspaces(name, owner_user_id, created_at) VALUES (?, ?, ?)",
                    (name, user_id, now.isoformat()),
                )
                workspace_id = int(cursor.lastrowid)
                connection.execute(
                    "INSERT INTO workspace_members(workspace_id, user_id, role, created_at) VALUES (?, ?, 'owner', ?)",
                    (workspace_id, user_id, now.isoformat()),
                )
        result = self.primary_workspace(user_id)
        assert result is not None
        return result

    def _identity_user(self, provider: str, subject: str) -> dict[str, Any] | None:
        placeholder = "%s" if self.backend == "postgres" else "?"
        with self.connect() as connection:
            row = connection.execute(
                f"""
                SELECT u.* FROM user_identities i
                JOIN users u ON u.id = i.user_id
                WHERE i.provider = {placeholder} AND i.subject = {placeholder}
                """,
                (provider, subject),
            ).fetchone()
        return dict(row) if row else None

    def _telegram_user(self, telegram_id: int) -> dict[str, Any] | None:
        placeholder = "%s" if self.backend == "postgres" else "?"
        with self.connect() as connection:
            row = connection.execute(
                f"SELECT * FROM users WHERE telegram_user_id = {placeholder}",
                (telegram_id,),
            ).fetchone()
        return dict(row) if row else None

    def _user_by_id(self, user_id: int) -> dict[str, Any] | None:
        placeholder = "%s" if self.backend == "postgres" else "?"
        with self.connect() as connection:
            row = connection.execute(
                f"SELECT * FROM users WHERE id = {placeholder}",
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def _insert_user(
        self,
        *,
        telegram_id: int,
        username: str | None,
        full_name: str,
        phone: str | None,
        now: datetime,
    ) -> int:
        # Imported users may use arbitrary IDs and the PostgreSQL schema was
        # intentionally created without a serial sequence. A random positive
        # 62-bit internal ID avoids MAX(id)+1 races and works in SQLite too.
        for _ in range(8):
            user_id = secrets.randbelow((1 << 62) - 1) + 1
            try:
                with self.connect() as connection:
                    if self.backend == "postgres":
                        connection.execute(
                            """
                            INSERT INTO users(
                                id, telegram_user_id, username, full_name, phone_number,
                                role, is_registered, registered_at, is_active, created_at
                            ) VALUES (%s, %s, %s, %s, %s, 'user', TRUE, %s, TRUE, %s)
                            """,
                            (user_id, telegram_id, username, full_name, phone, now, now),
                        )
                    else:
                        connection.execute(
                            """
                            INSERT INTO users(
                                id, telegram_user_id, username, full_name, phone_number,
                                role, is_registered, registered_at, is_active, created_at
                            ) VALUES (?, ?, ?, ?, ?, 'user', 1, ?, 1, ?)
                            """,
                            (
                                user_id,
                                telegram_id,
                                username,
                                full_name,
                                phone,
                                now.isoformat(),
                                now.isoformat(),
                            ),
                        )
                return user_id
            except Exception as exc:
                # Retry only ID collisions. A Telegram uniqueness or schema error
                # is deterministic and should be surfaced immediately.
                if "users_pkey" not in str(exc) and "UNIQUE constraint failed: users.id" not in str(exc):
                    raise
        raise RuntimeError("Unable to allocate internal user id")

    def _update_user_profile(
        self,
        *,
        user_id: int,
        telegram_id: int,
        username: str | None,
        full_name: str,
        phone: str | None,
        now: datetime,
    ) -> None:
        placeholder = "%s" if self.backend == "postgres" else "?"
        values = (
            telegram_id,
            username,
            full_name,
            phone,
            True if self.backend == "postgres" else 1,
            now if self.backend == "postgres" else now.isoformat(),
            user_id,
        )
        with self.connect() as connection:
            connection.execute(
                f"""
                UPDATE users SET telegram_user_id = {placeholder}, username = {placeholder},
                    full_name = {placeholder}, phone_number = COALESCE({placeholder}, phone_number),
                    is_registered = {placeholder}, registered_at = COALESCE(registered_at, {placeholder})
                WHERE id = {placeholder}
                """,
                values,
            )

    def _upsert_identity(
        self,
        *,
        provider: str,
        subject: str,
        user_id: int,
        claims: dict[str, Any],
        now: datetime,
    ) -> None:
        import json

        with self.connect() as connection:
            if self.backend == "postgres":
                from psycopg.types.json import Jsonb

                connection.execute(
                    """
                    INSERT INTO user_identities(provider, subject, user_id, claims_json, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT(provider, subject) DO UPDATE SET
                        user_id = EXCLUDED.user_id,
                        claims_json = EXCLUDED.claims_json,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (provider, subject, user_id, Jsonb(claims), now, now),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO user_identities(provider, subject, user_id, claims_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(provider, subject) DO UPDATE SET
                        user_id = excluded.user_id,
                        claims_json = excluded.claims_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        provider,
                        subject,
                        user_id,
                        json.dumps(claims, ensure_ascii=False),
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
