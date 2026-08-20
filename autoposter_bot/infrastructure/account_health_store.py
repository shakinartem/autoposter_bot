from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, ContextManager


class AccountHealthStore:
    """Workspace-scoped durable state for read-only social connection probes."""

    def __init__(self, *, backend: str, connect: Callable[[], ContextManager[Any]]) -> None:
        if backend not in {"sqlite", "postgres"}:
            raise ValueError(f"Unsupported account health backend: {backend}")
        self.backend = backend
        self.connect = connect

    def init_schema(self) -> None:
        with self.connect() as db:
            if self.backend == "postgres":
                db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS account_connection_health (
                        workspace_id BIGINT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                        account_id BIGINT NOT NULL,
                        platform TEXT NOT NULL,
                        status TEXT NOT NULL,
                        code TEXT NOT NULL,
                        message TEXT NOT NULL,
                        probe_method TEXT NOT NULL,
                        identity_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                        reconnect_required BOOLEAN NOT NULL DEFAULT FALSE,
                        token_expires_at TIMESTAMP NULL,
                        checked_at TIMESTAMP NOT NULL,
                        last_success_at TIMESTAMP NULL,
                        PRIMARY KEY(workspace_id, account_id)
                    )
                    """
                )
            else:
                # Older SQLite databases/tests may predate the explicit workspace-account
                # mapping migration. Health aggregation needs the table even when it is empty.
                db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS workspace_accounts (
                        workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                        account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY(workspace_id, account_id)
                    )
                    """
                )
                db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS account_connection_health (
                        workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                        account_id INTEGER NOT NULL,
                        platform TEXT NOT NULL,
                        status TEXT NOT NULL,
                        code TEXT NOT NULL,
                        message TEXT NOT NULL,
                        probe_method TEXT NOT NULL,
                        identity_json TEXT NOT NULL DEFAULT '{}',
                        reconnect_required INTEGER NOT NULL DEFAULT 0,
                        token_expires_at TEXT NULL,
                        checked_at TEXT NOT NULL,
                        last_success_at TEXT NULL,
                        PRIMARY KEY(workspace_id, account_id)
                    )
                    """
                )
            db.execute(
                """CREATE INDEX IF NOT EXISTS idx_account_connection_health_workspace_status
                ON account_connection_health(workspace_id, status, checked_at)"""
            )

    def list_workspaces(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT id, name FROM workspaces ORDER BY id").fetchall()
        return [{"id": int(row["id"]), "name": str(row["name"])} for row in rows]

    def upsert(
        self,
        *,
        workspace_id: int,
        account_id: int,
        platform: str,
        status: str,
        code: str,
        message: str,
        probe_method: str,
        identity: dict[str, Any] | None = None,
        reconnect_required: bool = False,
        token_expires_at: datetime | str | None = None,
        checked_at: datetime | None = None,
    ) -> dict[str, Any]:
        if status not in {"healthy", "degraded", "critical"}:
            raise ValueError(f"Unsupported account health status: {status}")
        checked = self._aware(checked_at or datetime.now(timezone.utc))
        checked_db = self._db_datetime(checked)
        expires_db = self._db_datetime(self._parse_datetime(token_expires_at)) if token_expires_at else None
        identity = dict(identity or {})
        ph = "%s" if self.backend == "postgres" else "?"
        with self.connect() as db:
            previous = db.execute(
                f"SELECT last_success_at FROM account_connection_health WHERE workspace_id = {ph} AND account_id = {ph}",
                (workspace_id, account_id),
            ).fetchone()
            previous_success = previous["last_success_at"] if previous else None
            last_success = checked_db if status == "healthy" else previous_success
            identity_value = self._json_db(identity)
            if self.backend == "postgres":
                db.execute(
                    """
                    INSERT INTO account_connection_health(
                        workspace_id, account_id, platform, status, code, message,
                        probe_method, identity_json, reconnect_required, token_expires_at,
                        checked_at, last_success_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT(workspace_id, account_id) DO UPDATE SET
                        platform = EXCLUDED.platform,
                        status = EXCLUDED.status,
                        code = EXCLUDED.code,
                        message = EXCLUDED.message,
                        probe_method = EXCLUDED.probe_method,
                        identity_json = EXCLUDED.identity_json,
                        reconnect_required = EXCLUDED.reconnect_required,
                        token_expires_at = EXCLUDED.token_expires_at,
                        checked_at = EXCLUDED.checked_at,
                        last_success_at = EXCLUDED.last_success_at
                    """,
                    (
                        workspace_id,
                        account_id,
                        platform.lower(),
                        status,
                        code,
                        message,
                        probe_method,
                        identity_value,
                        reconnect_required,
                        expires_db,
                        checked_db,
                        last_success,
                    ),
                )
            else:
                db.execute(
                    """
                    INSERT INTO account_connection_health(
                        workspace_id, account_id, platform, status, code, message,
                        probe_method, identity_json, reconnect_required, token_expires_at,
                        checked_at, last_success_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(workspace_id, account_id) DO UPDATE SET
                        platform = excluded.platform,
                        status = excluded.status,
                        code = excluded.code,
                        message = excluded.message,
                        probe_method = excluded.probe_method,
                        identity_json = excluded.identity_json,
                        reconnect_required = excluded.reconnect_required,
                        token_expires_at = excluded.token_expires_at,
                        checked_at = excluded.checked_at,
                        last_success_at = excluded.last_success_at
                    """,
                    (
                        workspace_id,
                        account_id,
                        platform.lower(),
                        status,
                        code,
                        message,
                        probe_method,
                        identity_value,
                        int(reconnect_required),
                        expires_db,
                        checked_db,
                        last_success,
                    ),
                )
        result = self.get(workspace_id=workspace_id, account_id=account_id)
        assert result is not None
        return result

    def get(self, *, workspace_id: int, account_id: int) -> dict[str, Any] | None:
        ph = "%s" if self.backend == "postgres" else "?"
        with self.connect() as db:
            row = db.execute(
                f"""
                SELECT h.*, a.name AS account_name, a.destination
                FROM account_connection_health h
                LEFT JOIN accounts a ON a.id = h.account_id
                WHERE h.workspace_id = {ph} AND h.account_id = {ph}
                """,
                (workspace_id, account_id),
            ).fetchone()
        return self._row(row) if row else None

    def list_workspace(self, *, workspace_id: int) -> list[dict[str, Any]]:
        ph = "%s" if self.backend == "postgres" else "?"
        with self.connect() as db:
            rows = db.execute(
                f"""
                SELECT h.*, a.name AS account_name, a.destination
                FROM account_connection_health h
                LEFT JOIN accounts a ON a.id = h.account_id
                WHERE h.workspace_id = {ph}
                ORDER BY CASE h.status WHEN 'critical' THEN 0 WHEN 'degraded' THEN 1 ELSE 2 END,
                         h.platform, a.name, h.account_id
                """,
                (workspace_id,),
            ).fetchall()
        return [self._row(row) for row in rows]

    def _row(self, row: Any) -> dict[str, Any]:
        item = dict(row)
        return {
            "workspace_id": int(item["workspace_id"]),
            "account_id": int(item["account_id"]),
            "account_name": item.get("account_name"),
            "destination": item.get("destination"),
            "platform": str(item["platform"]),
            "status": str(item["status"]),
            "code": str(item["code"]),
            "message": str(item["message"]),
            "probe_method": str(item["probe_method"]),
            "identity": self._json_value(item.get("identity_json")),
            "reconnect_required": bool(item.get("reconnect_required")),
            "token_expires_at": self._date_text(item.get("token_expires_at")),
            "checked_at": self._date_text(item.get("checked_at")),
            "last_success_at": self._date_text(item.get("last_success_at")),
        }

    def _json_db(self, value: dict[str, Any]) -> Any:
        if self.backend == "postgres":
            from psycopg.types.json import Jsonb

            return Jsonb(value)
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _json_value(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if value in (None, ""):
            return {}
        try:
            parsed = json.loads(str(value))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @classmethod
    def _parse_datetime(cls, value: datetime | str) -> datetime:
        if isinstance(value, datetime):
            return cls._aware(value)
        return cls._aware(datetime.fromisoformat(str(value).replace("Z", "+00:00")))

    def _db_datetime(self, value: datetime) -> Any:
        aware = self._aware(value)
        if self.backend == "postgres":
            return aware.replace(tzinfo=None)
        return aware.replace(tzinfo=None).isoformat()

    @staticmethod
    def _date_text(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc).isoformat()
        return str(value)
