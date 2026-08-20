from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, ContextManager

from autoposter_bot.infrastructure.auth_store import normalize_role, ROLE_LEVEL


INVITABLE_ROLES = ("viewer", "editor", "admin")


@dataclass(slots=True, frozen=True)
class WorkspaceInvitation:
    id: str
    workspace_id: int
    workspace_name: str
    role: str
    expires_at: datetime
    consumed: bool


class InvitationStore:
    def __init__(
        self,
        *,
        backend: str,
        connect: Callable[[], ContextManager[Any]],
    ) -> None:
        if backend not in {"sqlite", "postgres"}:
            raise ValueError(f"Unsupported invitation backend: {backend}")
        self.backend = backend
        self.connect = connect

    def init_schema(self) -> None:
        with self.connect() as connection:
            if self.backend == "postgres":
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS workspace_invitations (
                        id TEXT PRIMARY KEY,
                        token_hash TEXT NOT NULL UNIQUE,
                        workspace_id BIGINT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                        role TEXT NOT NULL,
                        created_by_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        expires_at TIMESTAMPTZ NOT NULL,
                        consumed_at TIMESTAMPTZ,
                        consumed_by_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
                        revoked_at TIMESTAMPTZ
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_workspace_invitations_workspace ON workspace_invitations(workspace_id, created_at DESC)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_workspace_invitations_active ON workspace_invitations(token_hash, expires_at)"
                )
            else:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS workspace_invitations (
                        id TEXT PRIMARY KEY,
                        token_hash TEXT NOT NULL UNIQUE,
                        workspace_id INTEGER NOT NULL,
                        role TEXT NOT NULL,
                        created_by_user_id INTEGER,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        consumed_at TEXT,
                        consumed_by_user_id INTEGER,
                        revoked_at TEXT,
                        FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
                        FOREIGN KEY(created_by_user_id) REFERENCES users(id) ON DELETE SET NULL,
                        FOREIGN KEY(consumed_by_user_id) REFERENCES users(id) ON DELETE SET NULL
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_workspace_invitations_workspace ON workspace_invitations(workspace_id, created_at DESC)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_workspace_invitations_active ON workspace_invitations(token_hash, expires_at)"
                )

    def issue(
        self,
        *,
        workspace_id: int,
        role: str,
        created_by_user_id: int | None,
        ttl_seconds: int = 7 * 24 * 60 * 60,
    ) -> tuple[str, WorkspaceInvitation]:
        normalized = normalize_role(role)
        if normalized not in INVITABLE_ROLES:
            raise ValueError(f"Unsupported invitation role: {role}")
        workspace = self._workspace(workspace_id)
        if workspace is None:
            raise ValueError("Workspace does not exist")
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=max(300, min(ttl_seconds, 30 * 24 * 60 * 60)))
        invitation_id = str(uuid.uuid4())
        token = f"awi_{secrets.token_urlsafe(40)}"
        token_hash = self.hash_token(token)
        values = (
            invitation_id,
            token_hash,
            workspace_id,
            normalized,
            created_by_user_id,
            self._db_datetime(now),
            self._db_datetime(expires_at),
        )
        with self.connect() as connection:
            placeholders = "%s" if self.backend == "postgres" else "?"
            connection.execute(
                f"""
                INSERT INTO workspace_invitations(
                    id, token_hash, workspace_id, role, created_by_user_id, created_at, expires_at
                ) VALUES ({','.join([placeholders] * 7)})
                """,
                values,
            )
        return token, WorkspaceInvitation(
            id=invitation_id,
            workspace_id=workspace_id,
            workspace_name=str(workspace["name"]),
            role=normalized,
            expires_at=expires_at,
            consumed=False,
        )

    def preview(self, token: str) -> WorkspaceInvitation | None:
        row = self._invite_row(token, lock=False)
        if row is None or not self._is_active(row):
            return None
        return self._view(row)

    def list_workspace(self, workspace_id: int, *, include_consumed: bool = False) -> list[dict[str, Any]]:
        placeholder = "%s" if self.backend == "postgres" else "?"
        where = "" if include_consumed else "AND i.consumed_at IS NULL AND i.revoked_at IS NULL AND i.expires_at > " + placeholder
        params: tuple[Any, ...] = (workspace_id,)
        if not include_consumed:
            params = (workspace_id, self._db_datetime(datetime.now(timezone.utc)))
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT i.id, i.workspace_id, i.role, i.created_by_user_id, i.created_at,
                       i.expires_at, i.consumed_at, i.consumed_by_user_id, i.revoked_at,
                       w.name AS workspace_name
                FROM workspace_invitations i
                JOIN workspaces w ON w.id = i.workspace_id
                WHERE i.workspace_id = {placeholder} {where}
                ORDER BY i.created_at DESC
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def accept(self, token: str, *, user_id: int) -> WorkspaceInvitation:
        token_hash = self.hash_token(token)
        placeholder = "%s" if self.backend == "postgres" else "?"
        now = datetime.now(timezone.utc)
        now_db = self._db_datetime(now)
        with self.connect() as connection:
            if self.backend == "sqlite":
                connection.execute("BEGIN IMMEDIATE")
            suffix = " FOR UPDATE" if self.backend == "postgres" else ""
            row = connection.execute(
                f"""
                SELECT i.*, w.name AS workspace_name
                FROM workspace_invitations i
                JOIN workspaces w ON w.id = i.workspace_id
                WHERE i.token_hash = {placeholder}{suffix}
                """,
                (token_hash,),
            ).fetchone()
            if row is None or not self._is_active(row, now=now):
                raise ValueError("Invitation is invalid, expired, revoked or already used")
            role = normalize_role(str(row["role"]))
            existing = connection.execute(
                f"SELECT role FROM workspace_members WHERE workspace_id = {placeholder} AND user_id = {placeholder}",
                (int(row["workspace_id"]), user_id),
            ).fetchone()
            if existing:
                current_role = normalize_role(str(existing["role"]))
                role = current_role if ROLE_LEVEL[current_role] >= ROLE_LEVEL[role] else role
                connection.execute(
                    f"UPDATE workspace_members SET role = {placeholder} WHERE workspace_id = {placeholder} AND user_id = {placeholder}",
                    (role, int(row["workspace_id"]), user_id),
                )
            else:
                connection.execute(
                    f"""
                    INSERT INTO workspace_members(workspace_id, user_id, role, created_at)
                    VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder})
                    """,
                    (int(row["workspace_id"]), user_id, role, now_db),
                )
            cursor = connection.execute(
                f"""
                UPDATE workspace_invitations
                SET consumed_at = {placeholder}, consumed_by_user_id = {placeholder}
                WHERE id = {placeholder} AND consumed_at IS NULL AND revoked_at IS NULL
                """,
                (now_db, user_id, str(row["id"])),
            )
            if not cursor.rowcount:
                raise ValueError("Invitation was already consumed")
            return WorkspaceInvitation(
                id=str(row["id"]),
                workspace_id=int(row["workspace_id"]),
                workspace_name=str(row["workspace_name"]),
                role=role,
                expires_at=self._parse_datetime(row["expires_at"]),
                consumed=True,
            )

    def revoke(self, invitation_id: str, *, workspace_id: int) -> bool:
        placeholder = "%s" if self.backend == "postgres" else "?"
        with self.connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE workspace_invitations SET revoked_at = {placeholder}
                WHERE id = {placeholder} AND workspace_id = {placeholder}
                  AND consumed_at IS NULL AND revoked_at IS NULL
                """,
                (self._db_datetime(datetime.now(timezone.utc)), invitation_id, workspace_id),
            )
        return bool(cursor.rowcount)

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _invite_row(self, token: str, *, lock: bool) -> Any | None:
        if not token.startswith("awi_"):
            return None
        placeholder = "%s" if self.backend == "postgres" else "?"
        suffix = " FOR UPDATE" if lock and self.backend == "postgres" else ""
        with self.connect() as connection:
            return connection.execute(
                f"""
                SELECT i.*, w.name AS workspace_name
                FROM workspace_invitations i
                JOIN workspaces w ON w.id = i.workspace_id
                WHERE i.token_hash = {placeholder}{suffix}
                """,
                (self.hash_token(token),),
            ).fetchone()

    def _workspace(self, workspace_id: int) -> Any | None:
        placeholder = "%s" if self.backend == "postgres" else "?"
        with self.connect() as connection:
            return connection.execute(
                f"SELECT id, name FROM workspaces WHERE id = {placeholder}",
                (workspace_id,),
            ).fetchone()

    def _is_active(self, row: Any, *, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return (
            row["consumed_at"] is None
            and row["revoked_at"] is None
            and self._parse_datetime(row["expires_at"]) > now
        )

    def _view(self, row: Any) -> WorkspaceInvitation:
        return WorkspaceInvitation(
            id=str(row["id"]),
            workspace_id=int(row["workspace_id"]),
            workspace_name=str(row["workspace_name"]),
            role=normalize_role(str(row["role"])),
            expires_at=self._parse_datetime(row["expires_at"]),
            consumed=row["consumed_at"] is not None,
        )

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
