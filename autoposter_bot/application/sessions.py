from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from autoposter_bot.infrastructure.auth_store import AuthStore, normalize_role


class WorkspaceSessionService:
    def __init__(self, auth_store: AuthStore) -> None:
        self.auth_store = auth_store

    def list_workspaces(self, user_id: int) -> list[dict[str, Any]]:
        placeholder = "%s" if self.auth_store.backend == "postgres" else "?"
        with self.auth_store.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT w.id, w.name, w.owner_user_id, w.created_at, wm.role
                FROM workspace_members wm
                JOIN workspaces w ON w.id = wm.workspace_id
                WHERE wm.user_id = {placeholder}
                ORDER BY CASE WHEN w.owner_user_id = {placeholder} THEN 0
                              WHEN wm.role = 'admin' THEN 1
                              WHEN wm.role IN ('editor', 'member') THEN 2
                              ELSE 3 END,
                         w.created_at,
                         w.id
                """,
                (user_id, user_id),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["role"] = normalize_role(str(item.get("role") or "viewer"))
            result.append(item)
        return result

    def switch_workspace(
        self,
        *,
        session_id: str,
        user_id: int,
        workspace_id: int,
    ) -> dict[str, Any]:
        membership = self.auth_store.get_membership(workspace_id, user_id)
        if membership is None or not bool(membership.get("is_active")):
            raise ValueError("User is not an active member of the target workspace")

        placeholder = "%s" if self.auth_store.backend == "postgres" else "?"
        now = datetime.now(timezone.utc)
        now_db: datetime | str = now if self.auth_store.backend == "postgres" else now.isoformat()
        with self.auth_store.connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE user_sessions
                SET workspace_id = {placeholder}, last_seen_at = {placeholder}
                WHERE id = {placeholder} AND user_id = {placeholder} AND revoked_at IS NULL
                """,
                (workspace_id, now_db, session_id, user_id),
            )
        if not cursor.rowcount:
            raise ValueError("Session is no longer active")
        return {
            "workspace_id": workspace_id,
            "user_id": user_id,
            "role": normalize_role(str(membership["role"])),
            "session_id": session_id,
        }

    def revoke_current(self, *, session_id: str, user_id: int) -> bool:
        placeholder = "%s" if self.auth_store.backend == "postgres" else "?"
        now = datetime.now(timezone.utc)
        now_db: datetime | str = now if self.auth_store.backend == "postgres" else now.isoformat()
        with self.auth_store.connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE user_sessions SET revoked_at = {placeholder}
                WHERE id = {placeholder} AND user_id = {placeholder} AND revoked_at IS NULL
                """,
                (now_db, session_id, user_id),
            )
        return bool(cursor.rowcount)
