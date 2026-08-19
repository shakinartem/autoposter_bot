from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, ContextManager
from uuid import uuid4


class OperationsStore:
    """Workspace-scoped operational health, recovery actions and immutable events."""

    def __init__(self, *, backend: str, connect: Callable[[], ContextManager[Any]]) -> None:
        if backend not in {"sqlite", "postgres"}:
            raise ValueError(f"Unsupported operations backend: {backend}")
        self.backend = backend
        self.connect = connect

    def init_schema(self) -> None:
        with self.connect() as db:
            if self.backend == "postgres":
                db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS operations_events (
                        id TEXT PRIMARY KEY,
                        workspace_id BIGINT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                        actor_user_id BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
                        event_type TEXT NOT NULL,
                        publication_id TEXT NULL REFERENCES publications_v2(id) ON DELETE SET NULL,
                        details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                        created_at TIMESTAMP NOT NULL
                    )
                    """
                )
                db.execute(
                    """CREATE INDEX IF NOT EXISTS idx_operations_events_workspace_created
                    ON operations_events(workspace_id, created_at DESC)"""
                )
                db.execute(
                    """CREATE INDEX IF NOT EXISTS idx_operations_events_publication
                    ON operations_events(publication_id, created_at DESC)"""
                )
            else:
                db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS operations_events (
                        id TEXT PRIMARY KEY,
                        workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                        actor_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
                        event_type TEXT NOT NULL,
                        publication_id TEXT NULL REFERENCES publications_v2(id) ON DELETE SET NULL,
                        details_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL
                    )
                    """
                )
                db.execute(
                    """CREATE INDEX IF NOT EXISTS idx_operations_events_workspace_created
                    ON operations_events(workspace_id, created_at DESC)"""
                )
                db.execute(
                    """CREATE INDEX IF NOT EXISTS idx_operations_events_publication
                    ON operations_events(publication_id, created_at DESC)"""
                )

    def workspace_overview(self, *, workspace_id: int, now: datetime | None = None) -> dict[str, Any]:
        # Keep standalone OperationsStore usage backwards-compatible: the account-health
        # table is optional at construction time but must exist before aggregation.
        from autoposter_bot.infrastructure.account_health_store import AccountHealthStore

        AccountHealthStore(backend=self.backend, connect=self.connect).init_schema()
        now = self._aware(now or datetime.now(timezone.utc))
        cutoff = now - timedelta(hours=24)
        stale_processing_cutoff = now - timedelta(minutes=30)
        ph = "%s" if self.backend == "postgres" else "?"
        now_db = self._db_datetime(now)
        cutoff_db = self._db_datetime(cutoff)

        with self.connect() as db:
            status_rows = db.execute(
                f"""
                SELECT p.status, COUNT(*) AS count
                FROM publications_v2 p
                JOIN platform_variants v ON v.id = p.variant_id
                JOIN content_items c ON c.id = v.content_id
                WHERE c.workspace_id = {ph}
                GROUP BY p.status
                """,
                (workspace_id,),
            ).fetchall()
            due = db.execute(
                f"""
                SELECT COUNT(*) AS count,
                       MIN(COALESCE(p.next_attempt_at, p.scheduled_at)) AS oldest_due_at
                FROM publications_v2 p
                JOIN platform_variants v ON v.id = p.variant_id
                JOIN content_items c ON c.id = v.content_id
                WHERE c.workspace_id = {ph}
                  AND p.status = 'scheduled'
                  AND p.scheduled_at IS NOT NULL
                  AND COALESCE(p.next_attempt_at, p.scheduled_at) <= {ph}
                """,
                (workspace_id, now_db),
            ).fetchone()
            counters = db.execute(
                f"""
                SELECT
                    SUM(CASE WHEN p.status = 'failed' AND p.last_error_code = 'unknown_publish_outcome' THEN 1 ELSE 0 END) AS unknown_outcomes,
                    SUM(CASE WHEN p.status = 'scheduled' AND p.next_attempt_at IS NOT NULL THEN 1 ELSE 0 END) AS retry_scheduled,
                    SUM(CASE WHEN p.status = 'processing' THEN 1 ELSE 0 END) AS processing
                FROM publications_v2 p
                JOIN platform_variants v ON v.id = p.variant_id
                JOIN content_items c ON c.id = v.content_id
                WHERE c.workspace_id = {ph}
                """,
                (workspace_id,),
            ).fetchone()
            processing_rows = db.execute(
                f"""
                SELECT p.metadata_json, p.created_at
                FROM publications_v2 p
                JOIN platform_variants v ON v.id = p.variant_id
                JOIN content_items c ON c.id = v.content_id
                WHERE c.workspace_id = {ph} AND p.status = 'processing'
                """,
                (workspace_id,),
            ).fetchall()
            attempts = db.execute(
                f"""
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN pa.status IN ('failed', 'invalid') THEN 1 ELSE 0 END) AS failed
                FROM publication_attempts pa
                JOIN publications_v2 p ON p.id = pa.publication_id
                JOIN platform_variants v ON v.id = p.variant_id
                JOIN content_items c ON c.id = v.content_id
                WHERE c.workspace_id = {ph}
                  AND pa.started_at >= {ph}
                """,
                (workspace_id, cutoff_db),
            ).fetchone()
            analytics = db.execute(
                f"""
                SELECT MAX(a.captured_at) AS latest_analytics_at
                FROM analytics_snapshots a
                JOIN publications_v2 p ON p.id = a.publication_id
                JOIN platform_variants v ON v.id = p.variant_id
                JOIN content_items c ON c.id = v.content_id
                WHERE c.workspace_id = {ph}
                """,
                (workspace_id,),
            ).fetchone()
            published = db.execute(
                f"""
                SELECT MAX(p.published_at) AS latest_published_at,
                       SUM(CASE WHEN p.published_at >= {ph} THEN 1 ELSE 0 END) AS published_24h
                FROM publications_v2 p
                JOIN platform_variants v ON v.id = p.variant_id
                JOIN content_items c ON c.id = v.content_id
                WHERE c.workspace_id = {ph}
                """,
                (cutoff_db, workspace_id),
            ).fetchone()
            account_health_rows = db.execute(
                f"""
                SELECT a.id AS account_id, h.status, h.code, h.checked_at, h.reconnect_required
                FROM accounts a
                JOIN workspaces w ON w.id = {ph}
                LEFT JOIN workspace_accounts wa ON wa.account_id = a.id AND wa.workspace_id = w.id
                LEFT JOIN account_connection_health h
                  ON h.workspace_id = w.id AND h.account_id = a.id
                WHERE a.owner_user_id = w.owner_user_id OR wa.workspace_id IS NOT NULL
                """,
                (workspace_id,),
            ).fetchall()

        statuses = {str(row["status"]): int(row["count"]) for row in status_rows}
        due = dict(due) if due is not None else {}
        counters = dict(counters) if counters is not None else {}
        attempts = dict(attempts) if attempts is not None else {}
        analytics = dict(analytics) if analytics is not None else {}
        published = dict(published) if published is not None else {}
        oldest_due = self._parse_datetime(due["oldest_due_at"]) if due.get("oldest_due_at") else None
        latest_analytics = self._parse_datetime(analytics["latest_analytics_at"]) if analytics.get("latest_analytics_at") else None
        latest_published = self._parse_datetime(published["latest_published_at"]) if published.get("latest_published_at") else None
        total_attempts = int(attempts.get("total") or 0)
        failed_attempts = int(attempts.get("failed") or 0)
        queue_lag = max(0, int((now - oldest_due).total_seconds())) if oldest_due else 0
        analytics_lag = max(0, int((now - latest_analytics).total_seconds())) if latest_analytics else None
        failure_rate = (failed_attempts / total_attempts) if total_attempts else 0.0

        unknown = int(counters.get("unknown_outcomes") or 0)
        processing = int(counters.get("processing") or 0)
        stale_processing = 0
        for processing_row in processing_rows:
            metadata = self._json_value(processing_row["metadata_json"])
            started_raw = (
                metadata.get("processing_started_at")
                or metadata.get("provider_tracking_recorded_at")
                or metadata.get("publish_started_at")
                or processing_row["created_at"]
            )
            try:
                started = self._parse_datetime(started_raw)
            except (TypeError, ValueError):
                continue
            if started <= stale_processing_cutoff:
                stale_processing += 1
        due_count = int(due.get("count") or 0)
        account_health_cutoff = now - timedelta(minutes=45)
        account_total = len(account_health_rows)
        account_critical = 0
        account_degraded = 0
        account_healthy = 0
        account_unprobed = 0
        account_stale = 0
        account_reconnect_required = 0
        for account_row in account_health_rows:
            item = dict(account_row)
            status = item.get("status")
            if not status:
                account_unprobed += 1
                continue
            if status == "critical":
                account_critical += 1
            elif status == "degraded":
                account_degraded += 1
            elif status == "healthy":
                account_healthy += 1
            if item.get("reconnect_required"):
                account_reconnect_required += 1
            checked_raw = item.get("checked_at")
            if checked_raw:
                try:
                    if self._parse_datetime(checked_raw) <= account_health_cutoff:
                        account_stale += 1
                except (TypeError, ValueError):
                    account_stale += 1
            else:
                account_stale += 1

        reasons: list[dict[str, Any]] = []
        if unknown:
            reasons.append({"code": "unknown_publish_outcome", "severity": "critical", "count": unknown})
        if queue_lag >= 300:
            reasons.append({"code": "queue_lag", "severity": "critical", "value": queue_lag})
        elif queue_lag >= 60:
            reasons.append({"code": "queue_lag", "severity": "degraded", "value": queue_lag})
        if stale_processing:
            reasons.append({"code": "stale_provider_processing", "severity": "critical", "count": stale_processing})
        if account_critical:
            reasons.append({
                "code": "social_connection_health",
                "severity": "critical",
                "count": account_critical,
                "details": {
                    "critical": account_critical,
                    "degraded": account_degraded,
                    "unprobed": account_unprobed,
                    "stale": account_stale,
                    "reconnect_required": account_reconnect_required,
                },
            })
        elif account_degraded or account_unprobed or account_stale:
            reasons.append({
                "code": "social_connection_health",
                "severity": "degraded",
                "count": account_degraded + account_unprobed + account_stale,
                "details": {
                    "critical": account_critical,
                    "degraded": account_degraded,
                    "unprobed": account_unprobed,
                    "stale": account_stale,
                    "reconnect_required": account_reconnect_required,
                },
            })
        if total_attempts >= 5 and failure_rate >= 0.50:
            reasons.append({"code": "attempt_failure_rate", "severity": "critical", "value": round(failure_rate, 4)})
        elif total_attempts >= 5 and failure_rate >= 0.20:
            reasons.append({"code": "attempt_failure_rate", "severity": "degraded", "value": round(failure_rate, 4)})

        health = "healthy"
        if any(item["severity"] == "critical" for item in reasons):
            health = "critical"
        elif reasons:
            health = "degraded"

        return {
            "health": health,
            "health_reasons": reasons,
            "generated_at": now.isoformat(),
            "publication_statuses": statuses,
            "queue": {
                "due_count": due_count,
                "oldest_due_at": oldest_due.isoformat() if oldest_due else None,
                "lag_seconds": queue_lag,
                "retry_scheduled": int(counters.get("retry_scheduled") or 0),
            },
            "reconciliation": {
                "processing": processing,
                "stale_processing": stale_processing,
                "unknown_outcomes": unknown,
            },
            "attempts_24h": {
                "total": total_attempts,
                "failed": failed_attempts,
                "failure_rate": round(failure_rate, 4),
            },
            "analytics": {
                "latest_snapshot_at": latest_analytics.isoformat() if latest_analytics else None,
                "lag_seconds": analytics_lag,
            },
            "social_connections": {
                "total": account_total,
                "healthy": account_healthy,
                "degraded": account_degraded,
                "critical": account_critical,
                "unprobed": account_unprobed,
                "stale": account_stale,
                "reconnect_required": account_reconnect_required,
            },
            "publishing": {
                "latest_published_at": latest_published.isoformat() if latest_published else None,
                "published_24h": int(published.get("published_24h") or 0),
            },
        }

    def list_reconciliation_items(self, *, workspace_id: int, limit: int = 100) -> list[dict[str, Any]]:
        ph = "%s" if self.backend == "postgres" else "?"
        with self.connect() as db:
            rows = db.execute(
                f"""
                SELECT p.id, p.status, p.platform, p.destination, p.provider_tracking_id,
                       p.external_post_id, p.external_url, p.published_at, p.attempt_count,
                       p.last_error_code, p.last_error_message, p.metadata_json,
                       p.scheduled_at, p.created_at, p.updated_at,
                       c.title AS content_title
                FROM publications_v2 p
                JOIN platform_variants v ON v.id = p.variant_id
                JOIN content_items c ON c.id = v.content_id
                WHERE c.workspace_id = {ph}
                  AND (
                    p.status = 'processing'
                    OR (p.status = 'failed' AND p.last_error_code = 'unknown_publish_outcome')
                    OR (p.status = 'failed' AND p.provider_tracking_id IS NOT NULL)
                  )
                ORDER BY p.updated_at ASC
                LIMIT {ph}
                """,
                (workspace_id, limit),
            ).fetchall()
        return [self._reconciliation_row(row) for row in rows]

    def resolve_publication(
        self,
        *,
        workspace_id: int,
        publication_id: str,
        action: str,
        actor_user_id: int | None,
        note: str,
        external_post_id: str | None = None,
        external_url: str | None = None,
        acknowledge_duplicate_risk: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = self._aware(now or datetime.now(timezone.utc))
        now_db = self._db_datetime(now)
        ph = "%s" if self.backend == "postgres" else "?"
        normalized_action = action.strip().lower()
        if normalized_action not in {"confirm_published", "mark_failed", "retry"}:
            raise ValueError("Unsupported reconciliation action")
        if len(note.strip()) < 3:
            raise ValueError("A reconciliation note is required")

        with self.connect() as db:
            lock = " FOR UPDATE OF p" if self.backend == "postgres" else ""
            row = db.execute(
                f"""
                SELECT p.*, c.workspace_id, c.title AS content_title
                FROM publications_v2 p
                JOIN platform_variants v ON v.id = p.variant_id
                JOIN content_items c ON c.id = v.content_id
                WHERE p.id = {ph} AND c.workspace_id = {ph}{lock}
                """,
                (publication_id, workspace_id),
            ).fetchone()
            if row is None:
                raise KeyError(publication_id)
            current = dict(row)
            status = str(current["status"])
            if status not in {"failed", "processing", "publishing"}:
                raise ValueError(f"Publication in status {status} does not require reconciliation")

            prior = {
                "status": status,
                "provider_tracking_id": current.get("provider_tracking_id"),
                "external_post_id": current.get("external_post_id"),
                "published_at": self._date_text(current.get("published_at")),
                "last_error_code": current.get("last_error_code"),
            }

            if normalized_action == "confirm_published":
                final_post_id = (external_post_id or current.get("external_post_id") or "").strip()
                if not final_post_id:
                    raise ValueError("external_post_id is required to confirm a remote publication")
                db.execute(
                    f"""
                    UPDATE publications_v2
                    SET status = 'published', external_post_id = {ph}, external_url = {ph},
                        published_at = COALESCE(published_at, {ph}), next_attempt_at = NULL,
                        last_error_code = NULL, last_error_message = NULL, updated_at = {ph}
                    WHERE id = {ph}
                    """,
                    (final_post_id, external_url or current.get("external_url"), now_db, now_db, publication_id),
                )
            elif normalized_action == "mark_failed":
                db.execute(
                    f"""
                    UPDATE publications_v2
                    SET status = 'failed', next_attempt_at = NULL,
                        last_error_code = 'manually_resolved_failed', last_error_message = {ph},
                        updated_at = {ph}
                    WHERE id = {ph}
                    """,
                    (note.strip(), now_db, publication_id),
                )
            else:
                if not acknowledge_duplicate_risk:
                    raise ValueError("Retry requires acknowledge_duplicate_risk=true")
                if current.get("provider_tracking_id") or current.get("external_post_id") or current.get("published_at"):
                    raise ValueError("Retry is blocked while a durable remote identity exists")
                db.execute(
                    f"""
                    UPDATE publications_v2
                    SET status = 'scheduled', scheduled_at = COALESCE(scheduled_at, {ph}),
                        next_attempt_at = {ph}, last_error_code = NULL, last_error_message = NULL,
                        updated_at = {ph}
                    WHERE id = {ph}
                    """,
                    (now_db, now_db, now_db, publication_id),
                )

            details = {
                "action": normalized_action,
                "note": note.strip(),
                "previous": prior,
                "external_post_id": external_post_id,
                "external_url": external_url,
                "acknowledge_duplicate_risk": acknowledge_duplicate_risk,
            }
            event_id = str(uuid4())
            if self.backend == "postgres":
                from psycopg.types.json import Jsonb

                details_value: Any = Jsonb(details)
            else:
                details_value = json.dumps(details, ensure_ascii=False)
            db.execute(
                f"""
                INSERT INTO operations_events(
                    id, workspace_id, actor_user_id, event_type, publication_id, details_json, created_at
                ) VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
                """,
                (
                    event_id,
                    workspace_id,
                    actor_user_id,
                    f"publication_reconciliation.{normalized_action}",
                    publication_id,
                    details_value,
                    now_db,
                ),
            )

            refreshed = db.execute(
                f"""
                SELECT p.id, p.status, p.platform, p.destination, p.provider_tracking_id,
                       p.external_post_id, p.external_url, p.published_at, p.attempt_count,
                       p.last_error_code, p.last_error_message, p.metadata_json,
                       p.scheduled_at, p.created_at, p.updated_at,
                       c.title AS content_title
                FROM publications_v2 p
                JOIN platform_variants v ON v.id = p.variant_id
                JOIN content_items c ON c.id = v.content_id
                WHERE p.id = {ph} AND c.workspace_id = {ph}
                """,
                (publication_id, workspace_id),
            ).fetchone()
            assert refreshed is not None
            result = self._reconciliation_row(refreshed)
            result["resolution_event_id"] = event_id
            return result

    def list_events(self, *, workspace_id: int, limit: int = 100) -> list[dict[str, Any]]:
        ph = "%s" if self.backend == "postgres" else "?"
        with self.connect() as db:
            rows = db.execute(
                f"""
                SELECT id, actor_user_id, event_type, publication_id, details_json, created_at
                FROM operations_events
                WHERE workspace_id = {ph}
                ORDER BY created_at DESC
                LIMIT {ph}
                """,
                (workspace_id, limit),
            ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "actor_user_id": int(row["actor_user_id"]) if row["actor_user_id"] is not None else None,
                "event_type": str(row["event_type"]),
                "publication_id": row["publication_id"],
                "details": self._json_value(row["details_json"]),
                "created_at": self._date_text(row["created_at"]),
            }
            for row in rows
        ]

    def _reconciliation_row(self, row: Any) -> dict[str, Any]:
        data = dict(row)
        return {
            "id": str(data["id"]),
            "status": str(data["status"]),
            "platform": str(data["platform"]),
            "destination": data.get("destination"),
            "content_title": data.get("content_title") or "",
            "provider_tracking_id": data.get("provider_tracking_id"),
            "external_post_id": data.get("external_post_id"),
            "external_url": data.get("external_url"),
            "published_at": self._date_text(data.get("published_at")),
            "attempt_count": int(data.get("attempt_count") or 0),
            "last_error_code": data.get("last_error_code"),
            "last_error_message": data.get("last_error_message"),
            "metadata": self._json_value(data.get("metadata_json")),
            "scheduled_at": self._date_text(data.get("scheduled_at")),
            "created_at": self._date_text(data.get("created_at")),
            "updated_at": self._date_text(data.get("updated_at")),
            "processing_started_at": self._processing_started_at(data),
            "can_retry": not bool(
                data.get("provider_tracking_id") or data.get("external_post_id") or data.get("published_at")
            ),
        }


    def _processing_started_at(self, data: dict[str, Any]) -> str | None:
        metadata = self._json_value(data.get("metadata_json"))
        value = (
            metadata.get("processing_started_at")
            or metadata.get("provider_tracking_recorded_at")
            or metadata.get("publish_started_at")
            or data.get("created_at")
        )
        return self._date_text(value)

    def _db_datetime(self, value: datetime):
        aware = self._aware(value)
        naive_utc = aware.replace(tzinfo=None)
        return naive_utc if self.backend == "postgres" else naive_utc.isoformat()

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _parse_datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            result = value
        else:
            result = datetime.fromisoformat(str(value))
        if result.tzinfo is None:
            return result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)

    @staticmethod
    def _json_value(value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        return json.loads(str(value) or "{}")

    @staticmethod
    def _date_text(value: Any) -> str | None:
        if value is None:
            return None
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
