from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb


class ContentFactoryLedger:
    """Cross-application identity, idempotency, media lineage and feedback outbox."""

    def __init__(self, persistence: Any) -> None:
        self.persistence = persistence
        self.backend = persistence.backend
        self.root = persistence.store.base
        self._sqlite_ingest_lock = threading.RLock()

    @contextmanager
    def delivery_lock(self, idempotency_key: str):
        """Serialize one delivery key across workers/processes before side effects.

        PostgreSQL advisory transaction locks make the initial idempotency check and all
        downstream media/content side effects race-safe across multiple API replicas.
        SQLite development mode uses a process-local lock.
        """
        if self.backend == "postgres":
            with self.root.connect() as connection:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (idempotency_key,),
                )
                yield
            return
        with self._sqlite_ingest_lock:
            yield

    def init_schema(self) -> None:
        root = Path(__file__).resolve().parents[2] / "migrations"
        name = "006_content_factory_bridge_postgres.sql" if self.backend == "postgres" else "005_content_factory_bridge_sqlite.sql"
        path = root / name
        if not path.exists():
            raise RuntimeError(f"Content Factory migration not found: {path}")
        with self.root.connect() as connection:
            if self.backend == "postgres":
                connection.execute(path.read_text(encoding="utf-8"))
            else:
                connection.executescript(path.read_text(encoding="utf-8"))

    def resolve_workspace(self, source_project_id: str, default_workspace_id: int | None) -> int:
        with self.root.connect() as connection:
            row = connection.execute(
                self._sql("SELECT workspace_id FROM content_factory_project_links WHERE source_project_id = ?"),
                (source_project_id,),
            ).fetchone()
            if row:
                return int(row["workspace_id"])
            if default_workspace_id is None:
                raise RuntimeError(
                    "Content Factory project is not mapped to an Autoposter workspace; "
                    "configure CONTENT_FACTORY_DEFAULT_WORKSPACE_ID for initial binding"
                )
            workspace = connection.execute(
                self._sql("SELECT id FROM workspaces WHERE id = ?"),
                (default_workspace_id,),
            ).fetchone()
            if not workspace:
                raise RuntimeError(f"Autoposter workspace does not exist: {default_workspace_id}")
            now = self._time(datetime.now(timezone.utc))
            connection.execute(
                self._sql(
                    """INSERT INTO content_factory_project_links(source_project_id, workspace_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?) ON CONFLICT(source_project_id) DO NOTHING"""
                ),
                (source_project_id, default_workspace_id, now, now),
            )
            row = connection.execute(
                self._sql("SELECT workspace_id FROM content_factory_project_links WHERE source_project_id = ?"),
                (source_project_id,),
            ).fetchone()
            if not row:
                raise RuntimeError("Unable to persist Content Factory workspace mapping")
            return int(row["workspace_id"])

    def get_receipt(self, idempotency_key: str) -> dict[str, Any] | None:
        with self.root.connect() as connection:
            row = connection.execute(
                self._sql("SELECT * FROM content_factory_receipts WHERE idempotency_key = ?"),
                (idempotency_key,),
            ).fetchone()
        result = self._row(row) if row else None
        if result:
            result["response_json"] = self._json_value(result.get("response_json"), {})
        return result

    def save_receipt(
        self,
        *,
        receipt_id: str,
        idempotency_key: str,
        payload_sha256: str,
        source_content_id: str,
        source_project_id: str,
        local_content_id: str,
        workspace_id: int,
        response: dict[str, Any],
    ) -> None:
        now = self._time(datetime.now(timezone.utc))
        with self.root.connect() as connection:
            connection.execute(
                self._sql(
                    """INSERT INTO content_factory_links(
                        source_content_id, source_project_id, local_content_id, workspace_id,
                        latest_payload_sha256, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_content_id) DO UPDATE SET
                      source_project_id = excluded.source_project_id,
                      local_content_id = excluded.local_content_id,
                      workspace_id = excluded.workspace_id,
                      latest_payload_sha256 = excluded.latest_payload_sha256,
                      updated_at = excluded.updated_at"""
                ),
                (source_content_id, source_project_id, local_content_id, workspace_id, payload_sha256, now),
            )
            connection.execute(
                self._sql(
                    """INSERT INTO content_factory_receipts(
                        id, idempotency_key, payload_sha256, source_content_id,
                        source_project_id, local_content_id, workspace_id, response_json,
                        received_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
                ),
                (
                    receipt_id,
                    idempotency_key,
                    payload_sha256,
                    source_content_id,
                    source_project_id,
                    local_content_id,
                    workspace_id,
                    self._json(response),
                    now,
                    now,
                ),
            )

    def get_media_link(self, asset_id: str, workspace_id: int) -> dict[str, Any] | None:
        with self.root.connect() as connection:
            row = connection.execute(
                self._sql("SELECT * FROM content_factory_media_links WHERE asset_id = ? AND workspace_id = ?"),
                (asset_id, workspace_id),
            ).fetchone()
        result = self._row(row) if row else None
        if result:
            result["metadata_json"] = self._json_value(result.get("metadata_json"), {})
        return result

    def save_media_link(self, *, asset_id: str, workspace_id: int, source: str, media_type: str, metadata: dict[str, Any]) -> None:
        with self.root.connect() as connection:
            connection.execute(
                self._sql(
                    """INSERT INTO content_factory_media_links(asset_id, workspace_id, source, media_type, metadata_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(asset_id, workspace_id) DO UPDATE SET
                      source = excluded.source,
                      media_type = excluded.media_type,
                      metadata_json = excluded.metadata_json"""
                ),
                (asset_id, workspace_id, source, media_type, self._json(metadata), self._time(datetime.now(timezone.utc))),
            )

    def publication_context(self, publication_id: str) -> dict[str, Any]:
        with self.root.connect() as connection:
            row = connection.execute(
                self._sql(
                    """SELECT p.id AS publication_id, p.platform, p.account_id, p.published_at, p.external_post_id, p.external_url,
                       pv.content_id, ci.metadata_json
                    FROM publications_v2 p
                    JOIN platform_variants pv ON pv.id = p.variant_id
                    JOIN content_items ci ON ci.id = pv.content_id
                    WHERE p.id = ?"""
                ),
                (publication_id,),
            ).fetchone()
        if not row:
            raise KeyError(f"Publication not found: {publication_id}")
        result = self._row(row)
        metadata = self._json_value(result.get("metadata_json"), {})
        factory = metadata.get("content_factory") or {}
        return {
            "source_content_id": factory.get("source_content_id"),
            "platform": result.get("platform"),
            "external_publication_id": result.get("external_post_id") or result.get("publication_id"),
            "external_url": result.get("external_url"),
            "account_id": result.get("account_id"),
            "published_at": self._datetime_value(result.get("published_at")) if result.get("published_at") else None,
            "payload_sha256": factory.get("payload_sha256"),
            "lineage": factory.get("lineage") if isinstance(factory.get("lineage"), dict) else None,
        }

    def analytics_candidates(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        """Return recent core analytics snapshots; feedback dedupe makes repeated scans safe."""
        placeholder = "%s" if self.backend == "postgres" else "?"
        with self.root.connect() as connection:
            rows = connection.execute(
                f"""SELECT a.publication_id, a.platform, a.milestone, a.collector_version,
                           a.metrics_json, a.captured_at
                    FROM analytics_snapshots a
                    ORDER BY a.captured_at DESC
                    LIMIT {placeholder}""",
                (max(1, min(limit, 5000)),),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for raw in rows:
            row = self._row(raw)
            row["metrics_json"] = self._json_value(row.get("metrics_json"), {})
            row["captured_at"] = self._datetime_value(row.get("captured_at"))
            result.append(row)
        return result

    def record_feedback(
        self,
        *,
        event_id: str,
        source_content_id: str,
        external_publication_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        now = self._time(datetime.now(timezone.utc))
        feedback_id = str(uuid4())
        with self.root.connect() as connection:
            if self.backend == "postgres":
                inserted = connection.execute(
                    """INSERT INTO content_factory_feedback_outbox(
                        id, event_id, source_content_id, external_publication_id,
                        payload_json, status, attempts, available_at, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, 'pending', 0, %s, %s, %s)
                    ON CONFLICT(event_id) DO NOTHING RETURNING id""",
                    (feedback_id, event_id, source_content_id, external_publication_id, Jsonb(payload), now, now, now),
                ).fetchone()
            else:
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO content_factory_feedback_outbox(
                        id, event_id, source_content_id, external_publication_id,
                        payload_json, status, attempts, available_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?)""",
                    (feedback_id, event_id, source_content_id, external_publication_id, json.dumps(payload, ensure_ascii=False), now, now, now),
                )
                inserted = feedback_id if cursor.rowcount else None
            if inserted:
                return {"status": "accepted", "event_id": event_id, "feedback_id": feedback_id}
            existing = connection.execute(
                self._sql("SELECT id, status FROM content_factory_feedback_outbox WHERE event_id = ?"),
                (event_id,),
            ).fetchone()
            if existing is None:
                raise RuntimeError("Feedback dedupe conflict could not be resolved")
            return {
                "status": "duplicate",
                "event_id": event_id,
                "feedback_id": existing["id"],
                "feedback_status": existing["status"],
            }

    def claim_feedback(self, *, limit: int) -> list[dict[str, Any]]:
        now = self._time(datetime.now(timezone.utc))
        with self.root.connect() as connection:
            suffix = " FOR UPDATE SKIP LOCKED" if self.backend == "postgres" else ""
            rows = connection.execute(
                self._sql(
                    """SELECT * FROM content_factory_feedback_outbox
                    WHERE status IN ('pending', 'failed') AND available_at <= ?
                    ORDER BY created_at ASC LIMIT ?"""
                ) + suffix,
                (now, limit),
            ).fetchall()
            claimed = []
            for raw in rows:
                row = self._row(raw)
                attempts = int(row.get("attempts") or 0) + 1
                connection.execute(
                    self._sql("UPDATE content_factory_feedback_outbox SET status = 'sending', attempts = ?, updated_at = ? WHERE id = ?"),
                    (attempts, now, row["id"]),
                )
                row["attempts"] = attempts
                row["payload_json"] = self._json_value(row.get("payload_json"), {})
                claimed.append(row)
            return claimed

    def mark_feedback_sent(self, feedback_id: str) -> None:
        now = self._time(datetime.now(timezone.utc))
        with self.root.connect() as connection:
            connection.execute(
                self._sql("UPDATE content_factory_feedback_outbox SET status = 'sent', sent_at = ?, last_error = NULL, updated_at = ? WHERE id = ?"),
                (now, now, feedback_id),
            )

    def mark_feedback_failed(self, feedback_id: str, *, attempts: int, error: str) -> None:
        now_dt = datetime.now(timezone.utc)
        available = now_dt + timedelta(seconds=min(3600, max(5, 2 ** min(attempts, 10))))
        with self.root.connect() as connection:
            connection.execute(
                self._sql("UPDATE content_factory_feedback_outbox SET status = 'failed', available_at = ?, last_error = ?, updated_at = ? WHERE id = ?"),
                (self._time(available), error[:2000], self._time(now_dt), feedback_id),
            )

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.backend == "postgres" else sql

    def _json(self, value: Any) -> Any:
        return Jsonb(value) if self.backend == "postgres" else json.dumps(value, ensure_ascii=False)

    def _time(self, value: datetime) -> Any:
        return value if self.backend == "postgres" else value.isoformat()

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        return dict(row)

    @staticmethod
    def _json_value(value: Any, default: Any) -> Any:
        if value is None:
            return default
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return default

    @staticmethod
    def _datetime_value(value: Any) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
