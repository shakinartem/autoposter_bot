from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime
from typing import Any

from autoposter_bot.domain.content import MediaAsset, PlatformVariant, Publication
from autoposter_bot.infrastructure.media_storage import MediaStorage
from autoposter_bot.models import MediaItem, PostJob, Target
from autoposter_bot.platforms.base import CapabilitySpec, PlatformAdapter, PublicationResult, ValidationIssue
from autoposter_bot.publishers.base import Publisher


class LegacyPublisherAdapter(PlatformAdapter):
    def __init__(
        self,
        publisher: Publisher,
        *,
        content_types: tuple[str, ...] = ("text", "image", "video", "carousel"),
        fields: dict[str, dict[str, Any]] | None = None,
        features: dict[str, bool] | None = None,
        limits: dict[str, int] | None = None,
        media_storage: MediaStorage | None = None,
    ) -> None:
        self.publisher = publisher
        self.platform = publisher.platform
        self._content_types = content_types
        self._fields = fields or {}
        self._features = features or {}
        self._limits = limits or {}
        self.media_storage = media_storage

    def capabilities(self) -> CapabilitySpec:
        return CapabilitySpec(
            platform=self.platform,
            content_types=self._content_types,
            fields=self._fields,
            features=self._features,
            limits=self._limits,
        )

    def validate(self, variant: PlatformVariant) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if variant.platform.lower() != self.platform.lower():
            issues.append(ValidationIssue("platform", f"Variant is for {variant.platform}, adapter is {self.platform}", "platform_mismatch"))
        if not variant.text and not variant.media:
            issues.append(ValidationIssue("content", "Variant must contain text or media", "empty_content"))
        for field_name, spec in self._fields.items():
            if spec.get("required") and variant.fields.get(field_name) in (None, ""):
                issues.append(ValidationIssue(field_name, f"{field_name} is required for {self.platform}", "required_field"))
        return issues

    def publish(self, variant: PlatformVariant, publication: Publication, *, account_options: dict[str, Any], dry_run: bool = False) -> PublicationResult:
        issues = self.validate(variant)
        if issues:
            return PublicationResult(ok=False, status="invalid", error_code=issues[0].code, error_message=issues[0].message)

        context = (
            self.media_storage.resolve_variant(variant, self.platform)
            if self.media_storage is not None
            else nullcontext(variant)
        )
        with context as resolved_variant:
            legacy_job = self._to_legacy_job(resolved_variant, publication, account_options)
            result = self.publisher.publish(legacy_job, legacy_job.targets[0], dry_run=dry_run)
        return PublicationResult(
            ok=result.ok,
            status="published" if result.ok else "failed",
            published_at=datetime.now() if result.ok and not dry_run else None,
            error_message=None if result.ok else result.detail,
            retryable=not result.ok,
            raw_response={"legacy_detail": result.detail},
        )

    def _to_legacy_job(self, variant: PlatformVariant, publication: Publication, account_options: dict[str, Any]) -> PostJob:
        media_items = [self._to_legacy_media(asset, index) for index, asset in enumerate(variant.media)]
        content_type = str(variant.fields.get("content_type") or self._infer_content_type(variant))
        return PostJob(
            post_id=publication.id,
            content_type=content_type,
            text=variant.text,
            media_items=media_items,
            scheduled_at=publication.scheduled_at,
            targets=[Target(
                platform=self.platform,
                destination=publication.destination,
                account_id=publication.account_id,
                options={**account_options, **variant.fields},
            )],
            metadata={
                "content_id": variant.content_id,
                "variant_id": variant.id,
                "publication_id": publication.id,
                "platform_fields": dict(variant.fields),
            },
        )

    @staticmethod
    def _to_legacy_media(asset: MediaAsset, index: int) -> MediaItem:
        return MediaItem(source=asset.source, media_type=asset.media_type, order_index=index, options=dict(asset.metadata))

    def _infer_content_type(self, variant: PlatformVariant) -> str:
        if not variant.media:
            return "text"
        if len(variant.media) > 1:
            return "instagram_carousel" if self.platform == "instagram" else "carousel"
        media_type = variant.media[0].media_type
        if self.platform == "instagram":
            return "instagram_video" if media_type == "video" else "instagram_feed_image"
        if self.platform == "tiktok" and media_type == "video":
            return "tiktok_video"
        return media_type
