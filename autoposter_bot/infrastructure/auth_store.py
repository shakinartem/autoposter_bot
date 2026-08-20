from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, ContextManager


CANONICAL_ROLES = ("viewer", "editor", "admin", "owner")
ROLE_LEVEL = {
    "viewer": 10,
    "editor": 20,
    "admin": 30,
    "owner": 40,
    "service": 100,
}


def normalize_role(role: str | None) -> str:
    value = (role or "").strip().lower()
    # Legacy workspace_members used `member`; keep those users productive after
    # migration instead of silently downgrading them to read-only access.
    if value == "member":
        return "editor"
    if value in CANONICAL_ROLES:
        return value
    return "viewer"


def role_allows(role: str, minimum: str) -> bool:
    return ROLE_LEVEL.get(normalize_role(role), 0) >= ROLE_LEVEL[minimum]


@dataclass(slots=True, frozen=True)
class SessionIdentity:
    session_id: str
    user_id: int
    workspace_id: int
    role: str
    expires_at: datetime


class AuthStore:
    """Membership and revocable opaque-session persistence.

    Session bearer tokens are generated with OS entropy and never stored in
    plaintext. Only SHA-256 token hashes are persisted, so a database read does
    not immediately become an authenticated browser session.
    """

    def __init__(
        self,
        *,
        backend: str,
        connect: Callable[[], ContextManager[Any]],
    ) -> None:
        if backend not in {"sqlite", "postgres"}:
            raise ValueError(f"Unsupported auth backend: {backend}")
        self.backend = backend
        self.connect = connect

    def init_schema(self) -> None:
        with self.connect() as connection:
            if self.backend == "postgres":
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_sessions (
                        id TEXT PRIMARY KEY,
                        token_hash TEXT NOT NULL UNIQUE,
                        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        workspace_id BIGINT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                        created_at TIMESTAMPTZ NOT NULL,
                        expires_at TIMESTAMPTZ NOT NULL,
                        last_seen_at TIMESTAMPTZ NOT NULL,
                        revoked_at TIMESTAMPTZ
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_user_sessions_token_active ON user_sessions(token_hash, expires_at)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_user_sessions_user_workspace ON user_sessions(user_id, workspace_id)"
                )
                connection.execute(
                    """
                    INSERT INTO workspace_members(workspace_id, user_id, role, created_at)
                    SELECT id, owner_user_id, 'owner', CURRENT_TIMESTAMP FROM workspaces
                    ON CONFLICT(workspace_id, user_id) DO UPDATE SET role = 'owner'
                    """
                )
            else:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_sessions (
                        id TEXT PRIMARY KEY,
                        token_hash TEXT NOT NULL UNIQUE,
                        user_id INTEGER NOT NULL,
                        workspace_id INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL,
                        revoked_at TEXT,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                        FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_user_sessions_token_active ON user_sessions(token_hash, expires_at)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_user_sessions_user_workspace ON user_sessions(user_id, workspace_id)"
                )
                now = self._db_datetime(datetime.now(timezone.utc))
                rows = connection.execute("SELECT id, owner_user_id FROM workspaces").fetchall()
                for row in rows:
                    connection.execute(
                        """
                        INSERT INTO workspace_members(workspace_id, user_id, role, created_at)
                        VALUES (?, ?, 'owner', ?)
                        ON CONFLICT(workspace_id, user_id) DO UPDATE SET role = 'owner'
                        """,
                        (int(row["id"]), int(row["owner_user_id"]), now),
                    )

    def get_membership(self, workspace_id: int, user_id: int) -> dict[str, Any] | None:
        placeholder = "%s" if self.backend == "postgres" else "?"
        with self.connect() as connection:
            row = connection.execute(
                f"""
                SELECT wm.workspace_id, wm.user_id, wm.role, wm.created_at,
                       u.username, u.full_name, u.is_active
                FROM workspace_members wm
                JOIN users u ON u.id = wm.user_id
                WHERE wm.workspace_id = {placeholder} AND wm.user_id = {placeholder}
                """,
                (workspace_id, user_id),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["role"] = normalize_role(result.get("role"))
        return result

    def list_members(self, workspace_id: int) -> list[dict[str, Any]]:
        placeholder = "%s" if self.backend == "postgres" else "?"
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT wm.workspace_id, wm.user_id, wm.role, wm.created_at,
                       u.username, u.full_name, u.telegram_user_id, u.is_active
                FROM workspace_members wm
                JOIN users u ON u.id = wm.user_id
                WHERE wm.workspace_id = {placeholder}
                ORDER BY wm.created_at, wm.user_id
                """,
                (workspace_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["role"] = normalize_role(item.get("role"))
            result.append(item)
        return result

    def set_membership(self, workspace_id: int, user_id: int, role: str) -> dict[str, Any]:
        normalized = normalize_role(role)
        if normalized != role.strip().lower() or normalized not in CANONICAL_ROLES:
            raise ValueError(f"Unsupported workspace role: {role}")
        if not self._workspace_exists(workspace_id):
            raise ValueError("Workspace does not exist")
        if not self._active_user_exists(user_id):
            raise ValueError("Active user does not exist")

        now = self._db_datetime(datetime.now(timezone.utc))
        with self.connect() as connection:
            if self.backend == "postgres":
                connection.execute(
                    """
                    INSERT INTO workspace_members(workspace_id, user_id, role, created_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT(workspace_id, user_id) DO UPDATE SET role = EXCLUDED.role
                    """,
                    (workspace_id, user_id, normalized, now),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO workspace_members(workspace_id, user_id, role, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(workspace_id, user_id) DO UPDATE SET role = excluded.role
                    """,
                    (workspace_id, user_id, normalized, now),
                )
        membership = self.get_membership(workspace_id, user_id)
        assert membership is not None
        return membership

    def remove_membership(self, workspace_id: int, user_id: int) -> bool:
        if self._is_workspace_owner(workspace_id, user_id):
            raise ValueError("Workspace owner membership cannot be removed")
        placeholder = "%s" if self.backend == "postgres" else "?"
        with self.connect() as connection:
            cursor = connection.execute(
                f"DELETE FROM workspace_members WHERE workspace_id = {placeholder} AND user_id = {placeholder}",
                (workspace_id, user_id),
            )
            connection.execute(
                f"UPDATE user_sessions SET revoked_at = {placeholder} WHERE workspace_id = {placeholder} AND user_id = {placeholder} AND revoked_at IS NULL",
                (
                    self._db_datetime(datetime.now(timezone.utc)),
                    workspace_id,
                    user_id,
                ),
            )
        return bool(cursor.rowcount)

    def create_session(
        self,
        *,
        user_id: int,
        workspace_id: int,
        ttl_seconds: int = 60 * 60 * 24 * 30,
    ) -> tuple[str, SessionIdentity]:
        membership = self.get_membership(workspace_id, user_id)
        if membership is None or not bool(membership.get("is_active")):
            raise ValueError("User is not an active member of this workspace")

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=max(300, ttl_seconds))
        session_id = str(uuid.uuid4())
        raw_token = f"aps_{secrets.token_urlsafe(48)}"
        token_hash = self.hash_token(raw_token)
        values = (
            session_id,
            token_hash,
            user_id,
            workspace_id,
            self._db_datetime(now),
            self._db_datetime(expires_at),
            self._db_datetime(now),
        )
        with self.connect() as connection:
            if self.backend == "postgres":
                connection.execute(
                    """
                    INSERT INTO user_sessions(
                        id, token_hash, user_id, workspace_id,
                        created_at, expires_at, last_seen_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    values,
                )
            else:
                connection.execute(
                    """
                    INSERT INTO user_sessions(
                        id, token_hash, user_id, workspace_id,
                        created_at, expires_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
        return raw_token, SessionIdentity(
            session_id=session_id,
            user_id=user_id,
            workspace_id=workspace_id,
            role=str(membership["role"]),
            expires_at=expires_at,
        )

    def resolve_session(self, raw_token: str) -> SessionIdentity | None:
        if not raw_token.startswith("aps_"):
            return None
        token_hash = self.hash_token(raw_token)
        placeholder = "%s" if self.backend == "postgres" else "?"
        with self.connect() as connection:
            row = connection.execute(
                f"""
                SELECT s.id, s.user_id, s.workspace_id, s.expires_at, s.last_seen_at,
                       wm.role, u.is_active
                FROM user_sessions s
                JOIN workspace_members wm
                  ON wm.workspace_id = s.workspace_id AND wm.user_id = s.user_id
                JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = {placeholder} AND s.revoked_at IS NULL
                """,
                (token_hash,),
            ).fetchone()
            if row is None or not bool(row["is_active"]):
                return None
            expires_at = self._parse_datetime(row["expires_at"])
            now = datetime.now(timezone.utc)
            if expires_at <= now:
                return None
            last_seen = self._parse_datetime(row["last_seen_at"])
            if last_seen <= now - timedelta(minutes=15):
                connection.execute(
                    f"UPDATE user_sessions SET last_seen_at = {placeholder} WHERE id = {placeholder}",
                    (self._db_datetime(now), row["id"]),
                )
        return SessionIdentity(
            session_id=str(row["id"]),
            user_id=int(row["user_id"]),
            workspace_id=int(row["workspace_id"]),
            role=normalize_role(str(row["role"])),
            expires_at=expires_at,
        )

    def revoke_session(self, raw_token: str) -> bool:
        token_hash = self.hash_token(raw_token)
        placeholder = "%s" if self.backend == "postgres" else "?"
        with self.connect() as connection:
            cursor = connection.execute(
                f"UPDATE user_sessions SET revoked_at = {placeholder} WHERE token_hash = {placeholder} AND revoked_at IS NULL",
                (self._db_datetime(datetime.now(timezone.utc)), token_hash),
            )
        return bool(cursor.rowcount)

    @staticmethod
    def hash_token(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    def _workspace_exists(self, workspace_id: int) -> bool:
        placeholder = "%s" if self.backend == "postgres" else "?"
        with self.connect() as connection:
            row = connection.execute(
                f"SELECT 1 FROM workspaces WHERE id = {placeholder}",
                (workspace_id,),
            ).fetchone()
        return row is not None

    def _active_user_exists(self, user_id: int) -> bool:
        placeholder = "%s" if self.backend == "postgres" else "?"
        with self.connect() as connection:
            row = connection.execute(
                f"SELECT is_active FROM users WHERE id = {placeholder}",
                (user_id,),
            ).fetchone()
        return row is not None and bool(row["is_active"])

    def _is_workspace_owner(self, workspace_id: int, user_id: int) -> bool:
        placeholder = "%s" if self.backend == "postgres" else "?"
        with self.connect() as connection:
            row = connection.execute(
                f"SELECT 1 FROM workspaces WHERE id = {placeholder} AND owner_user_id = {placeholder}",
                (workspace_id, user_id),
            ).fetchone()
        return row is not None

    def _db_datetime(self, value: datetime) -> datetime | str:
        normalized = value.astimezone(timezone.utc)
        return normalized if self.backend == "postgres" else normalized.isoformat()

    @staticmethod
    def _parse_datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
