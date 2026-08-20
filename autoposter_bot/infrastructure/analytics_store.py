from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, ContextManager


@dataclass(slots=True, frozen=True)
class AnalyticsSnapshot:
    id: int
    publication_id: str
    platform: str
    milestone: str
    collector_version: str
    metrics: dict[str, Any]
    captured_at: datetime


MILESTONES: tuple[tuple[str, timedelta], ...] = (
    ("1h", timedelta(hours=1)),
    ("6h", timedelta(hours=6)),
    ("24h", timedelta(hours=24)),
    ("72h", timedelta(hours=72)),
    ("7d", timedelta(days=7)),
)


class AnalyticsStore:
    """Append-only publication performance snapshots.

    Snapshots are immutable and deduplicated by publication + milestone +
    collector version. Workspace reads are always joined through
    publication -> variant -> master content, so one tenant cannot address
    another tenant's performance rows by guessing publication IDs.
    """

    def __init__(
        self,
        *,
        backend: str,
        connect: Callable[[], ContextManager[Any]],
    ) -> None:
        if backend not in {"sqlite", "postgres"}:
            raise ValueError(f"Unsupported analytics backend: {backend}")
        self.backend = backend
        self.connect = connect

    def init_schema(self) -> None:
        with self.connect() as connection:
            if self.backend == "postgres":
                table = connection.execute(
                    "SELECT to_regclass('public.analytics_snapshots') AS table_name"
                ).fetchone()
                if not table or table["table_name"] is None:
                    return
                connection.execute("ALTER TABLE analytics_snapshots ADD COLUMN IF NOT EXISTS platform TEXT")
                connection.execute("ALTER TABLE analytics_snapshots ADD COLUMN IF NOT EXISTS milestone TEXT")
                connection.execute("ALTER TABLE analytics_snapshots ADD COLUMN IF NOT EXISTS collector_version TEXT")
                connection.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_analytics_snapshot_milestone
                    ON analytics_snapshots(publication_id, milestone, collector_version)
                    WHERE milestone IS NOT NULL AND collector_version IS NOT NULL
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_analytics_snapshot_platform_captured
                    ON analytics_snapshots(platform, captured_at DESC)
                    """
                )
                return

            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(analytics_snapshots)").fetchall()
            }
            if not columns:
                return
            for name in ("platform", "milestone", "collector_version"):
                if name not in columns:
                    connection.execute(f"ALTER TABLE analytics_snapshots ADD COLUMN {name} TEXT")
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_analytics_snapshot_milestone
                ON analytics_snapshots(publication_id, milestone, collector_version)
                WHERE milestone IS NOT NULL AND collector_version IS NOT NULL
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_analytics_snapshot_platform_captured
                ON analytics_snapshots(platform, captured_at DESC)
                """
            )

    def append(
        self,
        *,
        publication_id: str,
        platform: str,
        milestone: str,
        metrics: dict[str, Any],
        collector_version: str,
        captured_at: datetime | None = None,
    ) -> bool:
        if milestone not in {name for name, _ in MILESTONES}:
            raise ValueError(f"Unsupported analytics milestone: {milestone}")
        if not collector_version.strip():
            raise ValueError("collector_version is required")
        captured_at = captured_at or datetime.now(timezone.utc)
        with self.connect() as connection:
            if self.backend == "postgres":
                from psycopg.types.json import Jsonb

                cursor = connection.execute(
                    """
                    INSERT INTO analytics_snapshots(
                        publication_id, platform, milestone, collector_version,
                        metrics_json, captured_at
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        publication_id,
                        platform,
                        milestone,
                        collector_version,
                        Jsonb(metrics),
                        self._db_datetime(captured_at),
                    ),
                )
            else:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO analytics_snapshots(
                        publication_id, platform, milestone, collector_version,
                        metrics_json, captured_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        publication_id,
                        platform,
                        milestone,
                        collector_version,
                        json.dumps(metrics, ensure_ascii=False, separators=(",", ":")),
                        self._db_datetime(captured_at),
                    ),
                )
        return bool(cursor.rowcount)

    def list_for_publication(
        self,
        *,
        workspace_id: int,
        publication_id: str,
    ) -> list[AnalyticsSnapshot]:
        placeholder = "%s" if self.backend == "postgres" else "?"
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT a.id, a.publication_id, a.platform, a.milestone,
                       a.collector_version, a.metrics_json, a.captured_at
                FROM analytics_snapshots a
                JOIN publications_v2 p ON p.id = a.publication_id
                JOIN platform_variants v ON v.id = p.variant_id
                JOIN content_items c ON c.id = v.content_id
                WHERE c.workspace_id = {placeholder} AND a.publication_id = {placeholder}
                ORDER BY a.captured_at, a.id
                """,
                (workspace_id, publication_id),
            ).fetchall()
        return [self._snapshot(row) for row in rows]

    def dataset(self, *, workspace_id: int, limit: int = 1000) -> list[dict[str, Any]]:
        placeholder = "%s" if self.backend == "postgres" else "?"
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    c.id AS content_id,
                    c.title AS master_title,
                    c.body AS master_body,
                    c.cta AS master_cta,
                    c.hashtags_json,
                    v.id AS variant_id,
                    v.platform,
                    v.title AS variant_title,
                    v.text AS variant_text,
                    v.fields_json,
                    p.id AS publication_id,
                    p.account_id,
                    p.destination,
                    p.scheduled_at,
                    p.published_at,
                    p.external_post_id,
                    a.milestone,
                    a.collector_version,
                    a.metrics_json,
                    a.captured_at
                FROM analytics_snapshots a
                JOIN publications_v2 p ON p.id = a.publication_id
                JOIN platform_variants v ON v.id = p.variant_id
                JOIN content_items c ON c.id = v.content_id
                WHERE c.workspace_id = {placeholder}
                ORDER BY a.captured_at DESC
                LIMIT {placeholder}
                """,
                (workspace_id, max(1, min(limit, 5000))),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            for key in ("hashtags_json", "fields_json", "metrics_json"):
                item[key.removesuffix("_json")] = self._json(item.pop(key))
            result.append(item)
        return result

    def due_candidates(self, *, now: datetime, limit: int = 250) -> list[dict[str, Any]]:
        """Return published rows that have at least one due missing milestone."""

        oldest = now - timedelta(days=8)
        placeholder = "%s" if self.backend == "postgres" else "?"
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT p.id AS publication_id, p.platform, p.account_id,
                       p.external_post_id, p.external_url, p.published_at,
                       v.content_id, c.workspace_id
                FROM publications_v2 p
                JOIN platform_variants v ON v.id = p.variant_id
                JOIN content_items c ON c.id = v.content_id
                WHERE p.status = 'published'
                  AND p.published_at IS NOT NULL
                  AND p.published_at >= {placeholder}
                  AND p.published_at <= {placeholder}
                ORDER BY p.published_at
                LIMIT {placeholder}
                """,
                (
                    self._db_datetime(oldest),
                    self._db_datetime(now),
                    max(1, min(limit, 1000)),
                ),
            ).fetchall()
        candidates: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            publication_id = str(item["publication_id"])
            completed = self.completed_milestones(publication_id)
            published_at = self._parse_datetime(item["published_at"])
            due = [
                name
                for name, delay in MILESTONES
                if name not in completed and published_at + delay <= now
            ]
            if due:
                item["published_at"] = published_at
                item["due_milestones"] = due
                candidates.append(item)
        return candidates

    def completed_milestones(self, publication_id: str) -> set[str]:
        placeholder = "%s" if self.backend == "postgres" else "?"
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT milestone FROM analytics_snapshots
                WHERE publication_id = {placeholder} AND milestone IS NOT NULL
                """,
                (publication_id,),
            ).fetchall()
        return {str(row["milestone"]) for row in rows}

    def _snapshot(self, row: Any) -> AnalyticsSnapshot:
        return AnalyticsSnapshot(
            id=int(row["id"]),
            publication_id=str(row["publication_id"]),
            platform=str(row["platform"] or ""),
            milestone=str(row["milestone"] or ""),
            collector_version=str(row["collector_version"] or ""),
            metrics=self._json(row["metrics_json"]),
            captured_at=self._parse_datetime(row["captured_at"]),
        )

    @staticmethod
    def _json(value: Any) -> Any:
        if isinstance(value, str):
            return json.loads(value)
        return value

    def _db_datetime(self, value: datetime) -> datetime | str:
        # Content OS publication timestamps are stored as naive UTC-like
        # TIMESTAMPs in PostgreSQL; keep analytics candidate comparisons aligned.
        normalized = value.astimezone(timezone.utc)
        if self.backend == "postgres":
            return normalized.replace(tzinfo=None)
        return normalized.isoformat()

    @staticmethod
    def _parse_datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
