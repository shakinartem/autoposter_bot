from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, ContextManager


class OperationsStore:
    """Read-only workspace operational health metrics."""

    def __init__(self, *, backend: str, connect: Callable[[], ContextManager[Any]]) -> None:
        if backend not in {"sqlite", "postgres"}:
            raise ValueError(f"Unsupported operations backend: {backend}")
        self.backend = backend
        self.connect = connect

    def workspace_overview(self, *, workspace_id: int, now: datetime | None = None) -> dict[str, Any]:
        now = self._aware(now or datetime.now(timezone.utc))
        cutoff = now - timedelta(hours=24)
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
                    SUM(CASE WHEN p.last_error_code = 'unknown_publish_outcome' THEN 1 ELSE 0 END) AS unknown_outcomes,
                    SUM(CASE WHEN p.status = 'scheduled' AND p.next_attempt_at IS NOT NULL THEN 1 ELSE 0 END) AS retry_scheduled,
                    SUM(CASE WHEN p.status = 'processing' THEN 1 ELSE 0 END) AS processing
                FROM publications_v2 p
                JOIN platform_variants v ON v.id = p.variant_id
                JOIN content_items c ON c.id = v.content_id
                WHERE c.workspace_id = {ph}
                """,
                (workspace_id,),
            ).fetchone()
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
        due_count = int(due.get("count") or 0)
        health = "healthy"
        if unknown > 0 or queue_lag >= 300:
            health = "critical"
        elif queue_lag >= 60 or (total_attempts >= 5 and failure_rate >= 0.20):
            health = "degraded"

        return {
            "health": health,
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
            "publishing": {
                "latest_published_at": latest_published.isoformat() if latest_published else None,
                "published_24h": int(published.get("published_24h") or 0),
            },
        }

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
