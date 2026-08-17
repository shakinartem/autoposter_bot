from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests

from autoposter_bot.infrastructure.content_store import SQLiteContentStore


class FeedbackNotLinked(RuntimeError):
    pass


class ContentFactoryFeedback:
    def __init__(
        self,
        store: SQLiteContentStore,
        *,
        endpoint: str = "",
        token: str = "",
        timeout_seconds: float = 20.0,
    ) -> None:
        self.store = store
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def init_schema(self) -> None:
        migration = self._migration_path()
        with self.store.connect() as connection:
            connection.executescript(migration.read_text(encoding="utf-8"))

    def record_snapshot(
        self,
        publication_id: str,
        *,
        metrics: dict[str, int | float],
        captured_at: datetime | None = None,
        event_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_metrics = self._clean_metrics(metrics)
        captured = (captured_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        event = event_id or f"autoposter-{uuid4()}"
        context = self._publication_context(publication_id)
        if not context["source_content_id"]:
            raise FeedbackNotLinked("Publication does not originate from Content Factory")

        payload = {
            "event_id": event,
            "source": "autoposter",
            "content_id": context["source_content_id"],
            "external_publication_id": context["external_publication_id"],
            "platform": context["platform"],
            "captured_at": captured.isoformat().replace("+00:00", "Z"),
            "metrics": clean_metrics,
            "metadata": {
                "publication_id": publication_id,
                "external_url": context["external_url"],
                **(metadata or {}),
            },
        }
        now = datetime.now(timezone.utc).isoformat()
        outbox_id = str(uuid4())
        with self.store.connect() as connection:
            existing = connection.execute(
                "SELECT id FROM content_factory_analytics_snapshots WHERE event_id = ?",
                (event,),
            ).fetchone()
            if existing:
                row = connection.execute(
                    "SELECT * FROM content_factory_feedback_outbox WHERE analytics_snapshot_id = ?",
                    (existing["id"],),
                ).fetchone()
                return {
                    "status": "duplicate",
                    "event_id": event,
                    "snapshot_id": existing["id"],
                    "feedback_status": row["status"] if row else "not_linked",
                }

            cursor = connection.execute(
                """
                INSERT INTO content_factory_analytics_snapshots (
                    publication_id, event_id, platform, metrics_json,
                    metadata_json, captured_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    publication_id,
                    event,
                    context["platform"],
                    json.dumps(clean_metrics, ensure_ascii=False),
                    json.dumps(metadata or {}, ensure_ascii=False),
                    captured.isoformat(),
                    now,
                ),
            )
            snapshot_id = int(cursor.lastrowid)
            connection.execute(
                """
                INSERT INTO content_factory_feedback_outbox (
                    id, analytics_snapshot_id, event_id, source_content_id,
                    external_publication_id, payload_json, status, attempts,
                    available_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?)
                """,
                (
                    outbox_id,
                    snapshot_id,
                    event,
                    context["source_content_id"],
                    context["external_publication_id"],
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    now,
                    now,
                ),
            )
        return {"status": "accepted", "event_id": event, "snapshot_id": snapshot_id, "feedback_id": outbox_id}

    def dispatch(self, *, limit: int = 50) -> dict[str, int]:
        if not self.endpoint or not self.token:
            return {"sent": 0, "failed": 0, "disabled": 1}
        now = datetime.now(timezone.utc)
        with self.store.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM content_factory_feedback_outbox
                WHERE status IN ('pending', 'failed') AND available_at <= ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (now.isoformat(), limit),
            ).fetchall()
        sent = failed = 0
        for row in rows:
            if self._dispatch_row(dict(row), now=now):
                sent += 1
            else:
                failed += 1
        return {"sent": sent, "failed": failed, "disabled": 0}

    def _dispatch_row(self, row: dict[str, Any], *, now: datetime) -> bool:
        with self.store.connect() as connection:
            current = connection.execute(
                "SELECT status, attempts FROM content_factory_feedback_outbox WHERE id = ?",
                (row["id"],),
            ).fetchone()
            if not current or current["status"] == "sent":
                return True
            attempt = int(current["attempts"] or 0) + 1
            connection.execute(
                "UPDATE content_factory_feedback_outbox SET status = 'sending', attempts = ?, updated_at = ? WHERE id = ?",
                (attempt, now.isoformat(), row["id"]),
            )
        try:
            response = requests.post(
                self.endpoint,
                json=json.loads(row["payload_json"]),
                headers={"X-Performance-Token": self.token},
                timeout=self.timeout_seconds,
            )
            if response.status_code not in {200, 202}:
                raise RuntimeError(f"Factory returned HTTP {response.status_code}: {response.text[:500]}")
            with self.store.connect() as connection:
                connection.execute(
                    """
                    UPDATE content_factory_feedback_outbox
                    SET status = 'sent', sent_at = ?, last_error = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (now.isoformat(), now.isoformat(), row["id"]),
                )
            return True
        except Exception as exc:
            delay = min(3600, max(5, 2 ** min(attempt, 10)))
            available_at = now + timedelta(seconds=delay)
            with self.store.connect() as connection:
                connection.execute(
                    """
                    UPDATE content_factory_feedback_outbox
                    SET status = 'failed', available_at = ?, last_error = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (available_at.isoformat(), str(exc)[:2000], now.isoformat(), row["id"]),
                )
            return False

    def _publication_context(self, publication_id: str) -> dict[str, Any]:
        with self.store.connect() as connection:
            row = connection.execute(
                """
                SELECT p.id AS publication_id, p.platform, p.external_post_id, p.external_url,
                       pv.content_id, ci.metadata_json
                FROM publications_v2 p
                JOIN platform_variants pv ON pv.id = p.variant_id
                JOIN content_items ci ON ci.id = pv.content_id
                WHERE p.id = ?
                """,
                (publication_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Publication not found: {publication_id}")
        content_metadata = json.loads(row["metadata_json"] or "{}")
        factory = content_metadata.get("content_factory") or {}
        return {
            "source_content_id": factory.get("source_content_id"),
            "platform": row["platform"],
            "external_publication_id": row["external_post_id"] or row["publication_id"],
            "external_url": row["external_url"],
        }

    @staticmethod
    def _clean_metrics(metrics: dict[str, int | float]) -> dict[str, int | float]:
        clean: dict[str, int | float] = {}
        for key, raw in metrics.items():
            name = key.strip().lower()
            if not name:
                continue
            value = float(raw)
            if value < 0:
                raise ValueError(f"Metric {name} cannot be negative")
            clean[name] = int(value) if value.is_integer() else value
        if not clean:
            raise ValueError("At least one numeric metric is required")
        return clean

    @staticmethod
    def _migration_path() -> Path:
        path = Path(__file__).resolve().parents[2] / "migrations" / "004_content_factory_performance_feedback.sql"
        if not path.exists():
            raise RuntimeError(f"Content Factory feedback migration not found: {path}")
        return path
