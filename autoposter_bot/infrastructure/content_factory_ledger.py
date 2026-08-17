from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb


class ContentFactoryLedger:
    def __init__(self, persistence: Any) -> None:
        self.persistence = persistence
        self.backend = persistence.backend
        self.root = persistence.store.base

    def init_schema(self) -> None:
        migrations = Path(__file__).resolve().parents[2] / "migrations"
        names = ["005_postgres_content_factory_bridge.sql"] if self.backend == "postgres" else [
            "003_content_factory_bridge.sql",
            "004_content_factory_performance_feedback.sql",
        ]
        with self.root.connect() as connection:
            for name in names:
                sql = (migrations / name).read_text(encoding="utf-8")
                if self.backend == "postgres":
                    connection.execute(sql)
                else:
                    connection.executescript(sql)

    def resolve_workspace(self, source_project_id: str, default_workspace_id: int | None) -> int:
        with self.root.connect() as connection:
            row = connection.execute(self._sql("SELECT workspace_id FROM content_factory_project_links WHERE source_project_id = ?"), (source_project_id,)).fetchone()
            if row:
                return int(row["workspace_id"])
            if default_workspace_id is None:
                raise RuntimeError("Content Factory project is not mapped to an Autoposter workspace")
            workspace = connection.execute(self._sql("SELECT id FROM workspaces WHERE id = ?"), (default_workspace_id,)).fetchone()
            if not workspace:
                raise RuntimeError(f"Autoposter workspace does not exist: {default_workspace_id}")
            now = self._time(datetime.now(timezone.utc))
            connection.execute(
                self._sql("INSERT INTO content_factory_project_links(source_project_id, workspace_id, created_at, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(source_project_id) DO NOTHING"),
                (source_project_id, default_workspace_id, now, now),
            )
            row = connection.execute(self._sql("SELECT workspace_id FROM content_factory_project_links WHERE source_project_id = ?"), (source_project_id,)).fetchone()
            if not row:
                raise RuntimeError("Unable to persist Content Factory workspace mapping")
            return int(row["workspace_id"])

    def get_receipt(self, key: str) -> dict[str, Any] | None:
        with self.root.connect() as connection:
            row = connection.execute(self._sql("SELECT * FROM content_factory_receipts WHERE idempotency_key = ?"), (key,)).fetchone()
        return dict(row) if row else None

    def save_receipt(self, *, receipt_id: str, idempotency_key: str, payload_sha256: str, source_content_id: str, source_project_id: str, local_content_id: str, workspace_id: int, response: dict[str, Any]) -> None:
        now = self._time(datetime.now(timezone.utc))
        with self.root.connect() as connection:
            connection.execute(self._sql("""INSERT INTO content_factory_links(source_content_id, source_project_id, local_content_id, workspace_id, latest_payload_sha256, updated_at)
                VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(source_content_id) DO UPDATE SET source_project_id=excluded.source_project_id, local_content_id=excluded.local_content_id, workspace_id=excluded.workspace_id, latest_payload_sha256=excluded.latest_payload_sha256, updated_at=excluded.updated_at"""),
                (source_content_id, source_project_id, local_content_id, workspace_id, payload_sha256, now))
            connection.execute(self._sql("""INSERT INTO content_factory_receipts(id, idempotency_key, payload_sha256, source_content_id, source_project_id, local_content_id, workspace_id, response_json, received_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""),
                (receipt_id, idempotency_key, payload_sha256, source_content_id, source_project_id, local_content_id, workspace_id, self._json(response), now, now))

    def get_media_link(self, asset_id: str, workspace_id: int) -> dict[str, Any] | None:
        with self.root.connect() as connection:
            row = connection.execute(self._sql("SELECT * FROM content_factory_media_links WHERE asset_id = ? AND workspace_id = ?"), (asset_id, workspace_id)).fetchone()
        result = dict(row) if row else None
        if result:
            result["metadata_json"] = self._json_value(result.get("metadata_json"), {})
        return result

    def save_media_link(self, *, asset_id: str, workspace_id: int, source: str, media_type: str, metadata: dict[str, Any]) -> None:
        with self.root.connect() as connection:
            connection.execute(self._sql("""INSERT INTO content_factory_media_links(asset_id, workspace_id, source, media_type, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(asset_id, workspace_id) DO UPDATE SET source=excluded.source, media_type=excluded.media_type, metadata_json=excluded.metadata_json"""),
                (asset_id, workspace_id, source, media_type, self._json(metadata), self._time(datetime.now(timezone.utc))))

    def publication_context(self, publication_id: str) -> dict[str, Any]:
        with self.root.connect() as connection:
            row = connection.execute(self._sql("""SELECT p.id AS publication_id, p.platform, p.external_post_id, p.external_url, pv.content_id, ci.metadata_json
                FROM publications_v2 p JOIN platform_variants pv ON pv.id=p.variant_id JOIN content_items ci ON ci.id=pv.content_id WHERE p.id = ?"""), (publication_id,)).fetchone()
        if not row:
            raise KeyError(f"Publication not found: {publication_id}")
        result = dict(row)
        factory = (self._json_value(result.get("metadata_json"), {}) or {}).get("content_factory") or {}
        return {"source_content_id": factory.get("source_content_id"), "platform": result.get("platform"), "external_publication_id": result.get("external_post_id") or result.get("publication_id"), "external_url": result.get("external_url")}

    def record_feedback(self, *, publication_id: str, event_id: str, platform: str | None, metrics: dict[str, int | float], metadata: dict[str, Any], captured_at: datetime, source_content_id: str, external_publication_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        now_dt = datetime.now(timezone.utc)
        now = self._time(now_dt)
        with self.root.connect() as connection:
            existing = connection.execute(self._sql("SELECT id FROM content_factory_analytics_snapshots WHERE event_id = ?"), (event_id,)).fetchone()
            if existing:
                outbox = connection.execute(self._sql("SELECT status FROM content_factory_feedback_outbox WHERE analytics_snapshot_id = ?"), (existing["id"],)).fetchone()
                return {"status": "duplicate", "event_id": event_id, "snapshot_id": int(existing["id"]), "feedback_status": outbox["status"] if outbox else "not_linked"}
            if self.backend == "postgres":
                row = connection.execute("INSERT INTO content_factory_analytics_snapshots(publication_id,event_id,platform,metrics_json,metadata_json,captured_at,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id", (publication_id,event_id,platform,Jsonb(metrics),Jsonb(metadata),captured_at,now_dt)).fetchone()
                snapshot_id = int(row["id"])
            else:
                cursor = connection.execute("INSERT INTO content_factory_analytics_snapshots(publication_id,event_id,platform,metrics_json,metadata_json,captured_at,created_at) VALUES (?,?,?,?,?,?,?)", (publication_id,event_id,platform,json.dumps(metrics),json.dumps(metadata),captured_at.isoformat(),now))
                snapshot_id = int(cursor.lastrowid)
            feedback_id = str(uuid4())
            connection.execute(self._sql("""INSERT INTO content_factory_feedback_outbox(id,analytics_snapshot_id,event_id,source_content_id,external_publication_id,payload_json,status,attempts,available_at,created_at,updated_at)
                VALUES (?,?,?,?,?,?,'pending',0,?,?,?)"""), (feedback_id,snapshot_id,event_id,source_content_id,external_publication_id,self._json(payload),now,now,now))
        return {"status":"accepted","event_id":event_id,"snapshot_id":snapshot_id,"feedback_id":feedback_id}

    def claim_feedback(self, *, limit: int) -> list[dict[str, Any]]:
        now = self._time(datetime.now(timezone.utc))
        with self.root.connect() as connection:
            suffix = " FOR UPDATE SKIP LOCKED" if self.backend == "postgres" else ""
            rows = connection.execute(self._sql("SELECT * FROM content_factory_feedback_outbox WHERE status IN ('pending','failed') AND available_at <= ? ORDER BY created_at ASC LIMIT ?") + suffix, (now, limit)).fetchall()
            result = []
            for raw in rows:
                row = dict(raw); attempts = int(row.get("attempts") or 0)+1
                connection.execute(self._sql("UPDATE content_factory_feedback_outbox SET status='sending',attempts=?,updated_at=? WHERE id=?"), (attempts,now,row["id"]))
                row["attempts"] = attempts; row["payload_json"] = self._json_value(row.get("payload_json"), {})
                result.append(row)
            return result

    def mark_feedback_sent(self, feedback_id: str) -> None:
        now = self._time(datetime.now(timezone.utc))
        with self.root.connect() as connection:
            connection.execute(self._sql("UPDATE content_factory_feedback_outbox SET status='sent',sent_at=?,last_error=NULL,updated_at=? WHERE id=?"), (now,now,feedback_id))

    def mark_feedback_failed(self, feedback_id: str, *, attempts: int, error: str) -> None:
        now_dt = datetime.now(timezone.utc); available = now_dt + timedelta(seconds=min(3600,max(5,2 ** min(attempts,10))))
        with self.root.connect() as connection:
            connection.execute(self._sql("UPDATE content_factory_feedback_outbox SET status='failed',available_at=?,last_error=?,updated_at=? WHERE id=?"), (self._time(available),error[:2000],self._time(now_dt),feedback_id))

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.backend == "postgres" else sql
    def _json(self, value: Any) -> Any:
        return Jsonb(value) if self.backend == "postgres" else json.dumps(value, ensure_ascii=False)
    def _time(self, value: datetime) -> Any:
        return value if self.backend == "postgres" else value.isoformat()
    @staticmethod
    def _json_value(value: Any, default: Any) -> Any:
        if value is None: return default
        if isinstance(value, (dict,list)): return value
        try: return json.loads(value)
        except (TypeError,json.JSONDecodeError): return default
