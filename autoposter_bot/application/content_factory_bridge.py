from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from autoposter_bot.apps.api.content_factory_contract import ContentPackageV1, package_hash
from autoposter_bot.application.content import ContentApplication
from autoposter_bot.domain.content import ContentItem, ContentStatus, MediaAsset
from autoposter_bot.infrastructure.content_factory_media import DurableMediaIngestor
from autoposter_bot.infrastructure.content_store import SQLiteContentStore


class IdempotencyConflict(RuntimeError):
    pass


class ContentFactoryBridge:
    def __init__(self, store: SQLiteContentStore, media_ingestor: DurableMediaIngestor) -> None:
        self.store = store
        self.application = ContentApplication(store)
        self.media_ingestor = media_ingestor

    def init_schema(self) -> None:
        migration = Path(__file__).resolve().parents[2] / "migrations" / "003_content_factory_bridge.sql"
        if not migration.exists():
            raise RuntimeError(f"Content Factory bridge migration not found: {migration}")
        with self.store.connect() as connection:
            connection.executescript(migration.read_text(encoding="utf-8"))

    def ingest(self, package: ContentPackageV1, *, idempotency_key: str, supported_platforms: set[str]) -> dict:
        digest = package_hash(package)
        existing = self._receipt(idempotency_key)
        if existing:
            if existing["payload_sha256"] != digest:
                raise IdempotencyConflict("Idempotency-Key was already used with a different payload")
            response = json.loads(existing["response_json"])
            response["idempotent_replay"] = True
            return response

        media_by_asset: dict[str, MediaAsset] = {}
        for variant in package.variants:
            for ref in variant.media:
                if ref.asset_id in media_by_asset:
                    continue
                local_path = self.media_ingestor.ingest(ref)
                media_by_asset[ref.asset_id] = MediaAsset(
                    id=f"cf-{ref.asset_id}",
                    source=str(local_path),
                    media_type=ref.type,
                    alt_text="",
                    metadata={
                        "content_factory_asset_id": ref.asset_id,
                        "mime_type": ref.mime_type,
                        "width": ref.width,
                        "height": ref.height,
                        "source_fingerprint": self.media_ingestor.source_fingerprint(ref),
                    },
                )

        local_content_id = str(uuid5(NAMESPACE_URL, f"content-factory:{package.content_id}"))
        master_media: list[MediaAsset] = []
        seen_media: set[str] = set()
        hashtags: list[str] = []
        seen_hashtags: set[str] = set()
        for variant in package.variants:
            for ref in variant.media:
                asset = media_by_asset[ref.asset_id]
                if asset.id not in seen_media:
                    master_media.append(asset)
                    seen_media.add(asset.id)
            for tag in variant.hashtags:
                if tag not in seen_hashtags:
                    hashtags.append(tag)
                    seen_hashtags.add(tag)

        item = self.store.get_content(local_content_id) or ContentItem(
            id=local_content_id,
            title=package.canonical.title,
            body=package.canonical.body or "",
        )
        item.title = package.canonical.title
        item.body = package.canonical.body or ""
        item.cta = package.canonical.cta or ""
        item.hashtags = hashtags
        item.media = master_media
        item.status = ContentStatus.READY
        item.updated_at = datetime.now()
        item.metadata = {
            **(item.metadata or {}),
            "content_factory": {
                "source_content_id": package.content_id,
                "source_project_id": package.project_id,
                "schema_version": package.schema_version,
                "content_type": package.canonical.content_type,
                "topic": package.canonical.topic,
                "goal": package.canonical.goal,
                "quality": package.quality.model_dump(mode="json"),
                "sources": [source.model_dump(mode="json") for source in package.sources],
                "payload_sha256": digest,
            },
        }
        self.store.save_content(item)

        variants = []
        unsupported = []
        for payload in package.variants:
            platform = payload.platform.lower()
            if platform not in supported_platforms and platform not in unsupported:
                unsupported.append(platform)
            variant = self.application.upsert_variant(
                local_content_id,
                platform,
                title=payload.title or "",
                text=payload.plain_text,
                media=[media_by_asset[ref.asset_id] for ref in payload.media],
                fields={"cta": payload.cta, "hashtags": payload.hashtags, "blocks": payload.blocks},
                sync_with_master=False,
                metadata={"content_factory": {"source_content_id": package.content_id, "payload_sha256": digest}},
            )
            variants.append({"id": variant.id, "platform": variant.platform})

        response = {
            "receipt_id": str(uuid5(NAMESPACE_URL, f"content-factory-receipt:{idempotency_key}")),
            "status": "accepted",
            "source_content_id": package.content_id,
            "content_id": local_content_id,
            "variants": variants,
            "unsupported_platforms": unsupported,
            "idempotent_replay": False,
        }
        self._save_link_and_receipt(package, idempotency_key, digest, local_content_id, response)
        return response

    def _receipt(self, idempotency_key: str):
        with self.store.connect() as connection:
            row = connection.execute("SELECT * FROM content_factory_receipts WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
            return dict(row) if row else None

    def _save_link_and_receipt(self, package: ContentPackageV1, idempotency_key: str, digest: str, local_content_id: str, response: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO content_factory_links (source_content_id, source_project_id, local_content_id, latest_payload_sha256, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_content_id) DO UPDATE SET
                    source_project_id = excluded.source_project_id,
                    local_content_id = excluded.local_content_id,
                    latest_payload_sha256 = excluded.latest_payload_sha256,
                    updated_at = excluded.updated_at
                """,
                (package.content_id, package.project_id, local_content_id, digest, now),
            )
            connection.execute(
                """
                INSERT INTO content_factory_receipts (
                    id, idempotency_key, payload_sha256, source_content_id, source_project_id,
                    local_content_id, response_json, received_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    response["receipt_id"], idempotency_key, digest, package.content_id, package.project_id,
                    local_content_id, json.dumps(response, ensure_ascii=False), now, now,
                ),
            )
