from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from autoposter_bot.application.content import ContentApplication
from autoposter_bot.apps.api.content_factory_contract import ContentPackageV1, package_hash
from autoposter_bot.domain.content import ContentItem, ContentStatus, MediaAsset
from autoposter_bot.infrastructure.content_factory_ledger import ContentFactoryLedger
from autoposter_bot.infrastructure.content_factory_media import DurableMediaIngestor


class IdempotencyConflict(RuntimeError):
    pass


class ContentFactoryBridge:
    def __init__(self, persistence: Any, ledger: ContentFactoryLedger, media_ingestor: DurableMediaIngestor, *, default_workspace_id: int | None) -> None:
        self.persistence = persistence
        self.ledger = ledger
        self.media_ingestor = media_ingestor
        self.default_workspace_id = default_workspace_id

    def ingest(self, package: ContentPackageV1, *, idempotency_key: str, supported_platforms: set[str]) -> dict[str, Any]:
        digest = package_hash(package)
        existing = self.ledger.get_receipt(idempotency_key)
        if existing:
            if existing["payload_sha256"] != digest:
                raise IdempotencyConflict("Idempotency-Key was already used with a different payload")
            raw = existing.get("response_json") or {}
            response = json.loads(raw) if isinstance(raw, str) else dict(raw)
            response["idempotent_replay"] = True
            return response

        workspace_id = self.ledger.resolve_workspace(package.project_id, self.default_workspace_id)
        scoped = self.persistence.scoped(workspace_id)
        if scoped.get_workspace() is None:
            raise RuntimeError(f"Mapped Autoposter workspace does not exist: {workspace_id}")
        application = ContentApplication(scoped)

        media_by_asset: dict[str, MediaAsset] = {}
        for variant in package.variants:
            for ref in variant.media:
                if ref.asset_id not in media_by_asset:
                    media_by_asset[ref.asset_id] = self.media_ingestor.ingest(ref, workspace_id=workspace_id)

        local_content_id = str(uuid5(NAMESPACE_URL, f"content-factory:{package.content_id}"))
        media: list[MediaAsset] = []
        seen_media: set[str] = set()
        hashtags: list[str] = []
        seen_tags: set[str] = set()
        for variant in package.variants:
            for ref in variant.media:
                asset = media_by_asset[ref.asset_id]
                if asset.id not in seen_media:
                    media.append(asset); seen_media.add(asset.id)
            for tag in variant.hashtags:
                if tag not in seen_tags:
                    hashtags.append(tag); seen_tags.add(tag)

        item = scoped.get_content(local_content_id) or ContentItem(id=local_content_id, title=package.canonical.title, body=package.canonical.body or "")
        item.title = package.canonical.title
        item.body = package.canonical.body or ""
        item.cta = package.canonical.cta or ""
        item.hashtags = hashtags
        item.media = media
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
        scoped.save_content(item)

        variants: list[dict[str, str]] = []
        unsupported: list[str] = []
        for payload in package.variants:
            platform = payload.platform.lower()
            if platform not in supported_platforms and platform not in unsupported:
                unsupported.append(platform)
            variant = application.upsert_variant(local_content_id, platform, title=payload.title or "", text=payload.plain_text, media=[media_by_asset[ref.asset_id] for ref in payload.media], fields={"cta": payload.cta, "hashtags": payload.hashtags, "blocks": payload.blocks}, sync_with_master=False, metadata={"content_factory": {"source_content_id": package.content_id, "payload_sha256": digest}})
            if variant is None:
                raise RuntimeError(f"Unable to persist variant: {platform}")
            variants.append({"id": variant.id, "platform": variant.platform})

        receipt_id = str(uuid5(NAMESPACE_URL, f"content-factory-receipt:{idempotency_key}"))
        response = {"receipt_id": receipt_id, "status": "accepted", "source_content_id": package.content_id, "workspace_id": workspace_id, "content_id": local_content_id, "variants": variants, "unsupported_platforms": unsupported, "idempotent_replay": False}
        self.ledger.save_receipt(receipt_id=receipt_id, idempotency_key=idempotency_key, payload_sha256=digest, source_content_id=package.content_id, source_project_id=package.project_id, local_content_id=local_content_id, workspace_id=workspace_id, response=response)
        return response
