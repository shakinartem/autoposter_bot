from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, ContextManager, Literal
from uuid import uuid4


AlertAction = Literal["notify", "resolve", "none"]


@dataclass(slots=True, frozen=True)
class HealthAlertDecision:
    workspace_id: int
    workspace_name: str
    action: AlertAction
    severity: str
    fingerprint: str | None
    overview: dict[str, Any]
    previously_notified: bool = False


class HealthAlertStore:
    """Durable, deduplicated workspace health alert state.

    One row per workspace represents the currently active alert fingerprint. The
    immutable state changes are also appended to operations_events, giving the
    team a timeline that can later train automated remediation/reconciliation.
    """

    def __init__(self, *, backend: str, connect: Callable[[], ContextManager[Any]]) -> None:
        if backend not in {"sqlite", "postgres"}:
            raise ValueError(f"Unsupported health-alert backend: {backend}")
        self.backend = backend
        self.connect = connect

    def init_schema(self) -> None:
        with self.connect() as db:
            if self.backend == "postgres":
                db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS operations_alert_state (
                        workspace_id BIGINT PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
                        fingerprint TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                        first_seen_at TIMESTAMP NOT NULL,
                        last_seen_at TIMESTAMP NOT NULL,
                        last_notified_at TIMESTAMP NULL,
                        resolved_at TIMESTAMP NULL,
                        last_delivery_error TEXT NULL
                    )
                    """
                )
            else:
                db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS operations_alert_state (
                        workspace_id INTEGER PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
                        fingerprint TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        details_json TEXT NOT NULL DEFAULT '{}',
                        first_seen_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL,
                        last_notified_at TEXT NULL,
                        resolved_at TEXT NULL,
                        last_delivery_error TEXT NULL
                    )
                    """
                )

    def list_workspaces(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT id, name FROM workspaces ORDER BY id").fetchall()
        return [{"id": int(row["id"]), "name": str(row["name"])} for row in rows]

    def evaluate(
        self,
        *,
        workspace_id: int,
        workspace_name: str,
        overview: dict[str, Any],
        now: datetime | None = None,
        reminder_seconds: int = 6 * 60 * 60,
    ) -> HealthAlertDecision:
        now = self._aware(now or datetime.now(timezone.utc))
        now_db = self._db_datetime(now)
        severity = str(overview.get("health") or "healthy").lower()
        reasons = overview.get("health_reasons") or []
        fingerprint = self._fingerprint(severity, reasons) if severity != "healthy" else None
        ph = "%s" if self.backend == "postgres" else "?"

        with self.connect() as db:
            row = db.execute(
                f"SELECT * FROM operations_alert_state WHERE workspace_id = {ph}",
                (workspace_id,),
            ).fetchone()
            current = dict(row) if row is not None else None

            if severity == "healthy":
                if current is None or current.get("resolved_at") is not None:
                    return HealthAlertDecision(
                        workspace_id, workspace_name, "none", severity, None, overview, False
                    )
                previously_notified = current.get("last_notified_at") is not None
                db.execute(
                    f"UPDATE operations_alert_state SET last_seen_at = {ph}, resolved_at = {ph}, last_delivery_error = NULL WHERE workspace_id = {ph}",
                    (now_db, now_db, workspace_id),
                )
                self._record_event(
                    db,
                    workspace_id=workspace_id,
                    event_type="health_alert.resolved",
                    details={
                        "previous_fingerprint": current.get("fingerprint"),
                        "previous_severity": current.get("severity"),
                    },
                    created_at=now_db,
                )
                return HealthAlertDecision(
                    workspace_id,
                    workspace_name,
                    "resolve" if previously_notified else "none",
                    severity,
                    None,
                    overview,
                    previously_notified,
                )

            assert fingerprint is not None
            payload = self._json_db_value(overview)
            if current is None or current.get("resolved_at") is not None or current.get("fingerprint") != fingerprint:
                previous_fingerprint = current.get("fingerprint") if current else None
                if self.backend == "postgres":
                    db.execute(
                        """
                        INSERT INTO operations_alert_state(
                            workspace_id, fingerprint, severity, details_json,
                            first_seen_at, last_seen_at, last_notified_at, resolved_at, last_delivery_error
                        ) VALUES (%s, %s, %s, %s, %s, %s, NULL, NULL, NULL)
                        ON CONFLICT(workspace_id) DO UPDATE SET
                            fingerprint = EXCLUDED.fingerprint,
                            severity = EXCLUDED.severity,
                            details_json = EXCLUDED.details_json,
                            first_seen_at = EXCLUDED.first_seen_at,
                            last_seen_at = EXCLUDED.last_seen_at,
                            last_notified_at = NULL,
                            resolved_at = NULL,
                            last_delivery_error = NULL
                        """,
                        (workspace_id, fingerprint, severity, payload, now_db, now_db),
                    )
                else:
                    db.execute(
                        """
                        INSERT INTO operations_alert_state(
                            workspace_id, fingerprint, severity, details_json,
                            first_seen_at, last_seen_at, last_notified_at, resolved_at, last_delivery_error
                        ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
                        ON CONFLICT(workspace_id) DO UPDATE SET
                            fingerprint = excluded.fingerprint,
                            severity = excluded.severity,
                            details_json = excluded.details_json,
                            first_seen_at = excluded.first_seen_at,
                            last_seen_at = excluded.last_seen_at,
                            last_notified_at = NULL,
                            resolved_at = NULL,
                            last_delivery_error = NULL
                        """,
                        (workspace_id, fingerprint, severity, payload, now_db, now_db),
                    )
                self._record_event(
                    db,
                    workspace_id=workspace_id,
                    event_type="health_alert.opened",
                    details={
                        "fingerprint": fingerprint,
                        "severity": severity,
                        "previous_fingerprint": previous_fingerprint,
                        "reasons": reasons,
                    },
                    created_at=now_db,
                )
                return HealthAlertDecision(
                    workspace_id, workspace_name, "notify", severity, fingerprint, overview, False
                )

            last_notified = self._parse_datetime(current.get("last_notified_at")) if current.get("last_notified_at") else None
            db.execute(
                f"UPDATE operations_alert_state SET severity = {ph}, details_json = {ph}, last_seen_at = {ph} WHERE workspace_id = {ph}",
                (severity, payload, now_db, workspace_id),
            )
            due = last_notified is None or last_notified <= now - timedelta(seconds=max(60, reminder_seconds))
            return HealthAlertDecision(
                workspace_id,
                workspace_name,
                "notify" if due else "none",
                severity,
                fingerprint,
                overview,
                last_notified is not None,
            )

    def mark_notified(
        self,
        *,
        workspace_id: int,
        recipient_count: int,
        now: datetime | None = None,
    ) -> None:
        now = self._aware(now or datetime.now(timezone.utc))
        now_db = self._db_datetime(now)
        ph = "%s" if self.backend == "postgres" else "?"
        with self.connect() as db:
            row = db.execute(
                f"SELECT fingerprint, severity FROM operations_alert_state WHERE workspace_id = {ph}",
                (workspace_id,),
            ).fetchone()
            if row is None:
                return
            db.execute(
                f"UPDATE operations_alert_state SET last_notified_at = {ph}, last_delivery_error = NULL WHERE workspace_id = {ph}",
                (now_db, workspace_id),
            )
            self._record_event(
                db,
                workspace_id=workspace_id,
                event_type="health_alert.notified",
                details={
                    "fingerprint": row["fingerprint"],
                    "severity": row["severity"],
                    "recipient_count": recipient_count,
                },
                created_at=now_db,
            )

    def mark_delivery_error(self, *, workspace_id: int, error: str) -> None:
        ph = "%s" if self.backend == "postgres" else "?"
        with self.connect() as db:
            db.execute(
                f"UPDATE operations_alert_state SET last_delivery_error = {ph} WHERE workspace_id = {ph}",
                (error[:1000], workspace_id),
            )

    def get_state(self, workspace_id: int) -> dict[str, Any] | None:
        ph = "%s" if self.backend == "postgres" else "?"
        with self.connect() as db:
            row = db.execute(
                f"SELECT * FROM operations_alert_state WHERE workspace_id = {ph}",
                (workspace_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["details"] = self._json_value(result.pop("details_json", None))
        return result

    @staticmethod
    def _fingerprint(severity: str, reasons: list[dict[str, Any]]) -> str:
        # Counts/lag values intentionally stay out of the fingerprint. Otherwise
        # every poll would create a new incident and defeat alert deduplication.
        stable = {
            "severity": severity,
            "reasons": sorted(str(item.get("code") or "unknown") for item in reasons),
        }
        raw = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()

    def _record_event(
        self,
        db: Any,
        *,
        workspace_id: int,
        event_type: str,
        details: dict[str, Any],
        created_at: Any,
    ) -> None:
        ph = "%s" if self.backend == "postgres" else "?"
        db.execute(
            f"""
            INSERT INTO operations_events(
                id, workspace_id, actor_user_id, event_type, publication_id, details_json, created_at
            ) VALUES ({ph}, {ph}, NULL, {ph}, NULL, {ph}, {ph})
            """,
            (str(uuid4()), workspace_id, event_type, self._json_db_value(details), created_at),
        )

    def _json_db_value(self, value: dict[str, Any]) -> Any:
        if self.backend == "postgres":
            from psycopg.types.json import Jsonb

            return Jsonb(value)
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _json_value(value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        return json.loads(str(value) or "{}")

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @classmethod
    def _parse_datetime(cls, value: Any) -> datetime:
        if isinstance(value, datetime):
            return cls._aware(value)
        return cls._aware(datetime.fromisoformat(str(value)))

    def _db_datetime(self, value: datetime) -> Any:
        value = self._aware(value).replace(tzinfo=None)
        return value if self.backend == "postgres" else value.isoformat()
