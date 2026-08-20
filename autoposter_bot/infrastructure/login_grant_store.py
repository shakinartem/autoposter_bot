from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, ContextManager


@dataclass(slots=True, frozen=True)
class LoginGrant:
    user_id: int
    workspace_id: int
    return_path: str


class LoginGrantStore:
    """One-time bridge between identity callback and browser session cookie.

    The identity provider callback never exposes the long-lived browser session
    token in a redirect URL. It issues an `apg_` grant instead; only the grant
    hash is persisted, it expires quickly, and it is consumed exactly once.
    """

    def __init__(
        self,
        *,
        backend: str,
        connect: Callable[[], ContextManager[Any]],
        ttl_seconds: int = 120,
    ) -> None:
        if backend not in {"sqlite", "postgres"}:
            raise ValueError(f"Unsupported login grant backend: {backend}")
        self.backend = backend
        self.connect = connect
        self.ttl_seconds = max(30, min(ttl_seconds, 600))

    def init_schema(self) -> None:
        with self.connect() as connection:
            if self.backend == "postgres":
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS login_grants (
                        token_hash TEXT PRIMARY KEY,
                        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        workspace_id BIGINT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                        return_path TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        expires_at TIMESTAMPTZ NOT NULL,
                        consumed_at TIMESTAMPTZ
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_login_grants_expires ON login_grants(expires_at)"
                )
            else:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS login_grants (
                        token_hash TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        workspace_id INTEGER NOT NULL,
                        return_path TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        consumed_at TEXT,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                        FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_login_grants_expires ON login_grants(expires_at)"
                )

    def issue(self, *, user_id: int, workspace_id: int, return_path: str = "/") -> str:
        raw = f"apg_{secrets.token_urlsafe(40)}"
        token_hash = self.hash_token(raw)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=self.ttl_seconds)
        path = self.safe_return_path(return_path)
        with self.connect() as connection:
            if self.backend == "postgres":
                connection.execute(
                    """
                    INSERT INTO login_grants(
                        token_hash, user_id, workspace_id, return_path, created_at, expires_at
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (token_hash, user_id, workspace_id, path, now, expires_at),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO login_grants(
                        token_hash, user_id, workspace_id, return_path, created_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        token_hash,
                        user_id,
                        workspace_id,
                        path,
                        now.isoformat(),
                        expires_at.isoformat(),
                    ),
                )
        return raw

    def consume(self, raw: str) -> LoginGrant | None:
        if not raw.startswith("apg_"):
            return None
        token_hash = self.hash_token(raw)
        placeholder = "%s" if self.backend == "postgres" else "?"
        now = datetime.now(timezone.utc)
        now_db = now if self.backend == "postgres" else now.isoformat()

        with self.connect() as connection:
            suffix = " FOR UPDATE" if self.backend == "postgres" else ""
            row = connection.execute(
                f"""
                SELECT token_hash, user_id, workspace_id, return_path, expires_at, consumed_at
                FROM login_grants
                WHERE token_hash = {placeholder}{suffix}
                """,
                (token_hash,),
            ).fetchone()
            if row is None or row["consumed_at"] is not None:
                return None
            expires_at = self._parse_datetime(row["expires_at"])
            if expires_at <= now:
                connection.execute(
                    f"DELETE FROM login_grants WHERE token_hash = {placeholder}",
                    (token_hash,),
                )
                return None
            cursor = connection.execute(
                f"""
                UPDATE login_grants SET consumed_at = {placeholder}
                WHERE token_hash = {placeholder} AND consumed_at IS NULL
                """,
                (now_db, token_hash),
            )
            if not cursor.rowcount:
                return None
            return LoginGrant(
                user_id=int(row["user_id"]),
                workspace_id=int(row["workspace_id"]),
                return_path=self.safe_return_path(str(row["return_path"] or "/")),
            )

    @staticmethod
    def hash_token(raw: str) -> str:
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def safe_return_path(value: str) -> str:
        value = value.strip()
        if not value.startswith("/") or value.startswith("//"):
            return "/"
        return value[:500]

    @staticmethod
    def _parse_datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
