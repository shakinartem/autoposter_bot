from __future__ import annotations

from datetime import datetime
from typing import Any

from autoposter_bot.domain.content import MediaAsset, PlatformVariant, Publication
from autoposter_bot.models import MediaItem, PostJob, Target
from autoposter_bot.platforms.base import (
    CapabilitySpec,
    PlatformAdapter,
    PublicationResult,
    ValidationIssue,
)
from autoposter_bot.publishers.base import Publisher


class LegacyPublisherAdapter(PlatformAdapter):
    """Bridge old Publisher implementations into the new platform contract.

    This keeps the current Telegram/VK/Instagram/TikTok integrations usable while
    the product is migrated to Content -> Variant -> Publication.
    """

    def __init__(
        self,
        publisher: Publisher,
        *,
        content_types: tuple[str, ...] = ("text", "image", "video", "carousel"),
        features: dict[str, bool] | None = None,
        limits: dict[str, int] | None = None,
    ) -> None:
        self.publisher = publisher
        self.platform = publisher.platform
        self._content_types = content_types
        self._features = features or {}
        self._limits = limits or {}

    def capabilities(self) -> CapabilitySpec:
        return CapabilitySpec(
            platform=self.platform,
            content_types=self._content_types,
            features=self._features,
            limits=self._limits,
        )

    def validate(self, variant: PlatformVariant) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if variant.platform.lower() != self.platform.lower():
            issues.append(
                ValidationIssue(
                    field="platform",
                    code="platform_mismatch",
                    message=f"Variant is for {variant.platform}, adapter is {self.platform}",
                )
            )
        if not variant.text and not variant.media:
            issues.append(
                ValidationIssue(
                    field="content",
                    code="empty_content",
                    message="Variant must contain text or media",
                )
            )
        return issues

    def publish(
        self,
        variant: PlatformVariant,
        publication: Publication,
        *,
        account_options: dict[str, Any],
        dry_run: bool = False,
    ) -> PublicationResult:
        issues = self.validate(variant)
        if issues:
            return PublicationResult(
                ok=False,
                status="invalid",
                error_code=issues[0].code,
                error_message=issues[0].message,
            )

        legacy_job = self._to_legacy_job(variant, publication, account_options)
        target = legacy_job.targets[0]
        result = self.publisher.publish(legacy_job, target, dry_run=dry_run)
        return PublicationResult(
            ok=result.ok,
            status="published" if result.ok else "failed",
            published_at=datetime.now() if result.ok and not dry_run else None,
            error_message=None if result.ok else result.detail,
            retryable=not result.ok,
            raw_response={"legacy_detail": result.detail},
        )

    def _to_legacy_job(
        self,
        variant: PlatformVariant,
        publication: Publication,
        account_options: dict[str, Any],
    ) -> PostJob:
        media_items = [self._to_legacy_media(asset, index) for index, asset in enumerate(variant.media)]
        content_type = str(variant.fields.get("content_type") or self._infer_content_type(variant))
        return PostJob(
            post_id=publication.id,
            content_type=content_type,
            text=variant.text,
            media_items=media_items,
            scheduled_at=publication.scheduled_at,
            targets=[
                Target(
                    platform=self.platform,
                    destination=publication.destination,
                    account_id=publication.account_id,
                    options=account_options,
                )
            ],
            metadata={
                "content_id": variant.content_id,
                "variant_id": variant.id,
                "publication_id": publication.id,
            },
        )

    @staticmethod
    def _to_legacy_media(asset: MediaAsset, index: int) -> MediaItem:
        return MediaItem(
            source=asset.source,
            media_type=asset.media_type,
            order_index=index,
            options=dict(asset.metadata),
        )

    def _infer_content_type(self, variant: PlatformVariant) -> str:
        if not variant.media:
            return "text"
        if len(variant.media) > 1:
            if self.platform == "instagram":
                return "instagram_carousel"
            return "carousel"
        media_type = variant.media[0].media_type
        if self.platform == "instagram":
            return "instagram_video" if media_type == "video" else "instagram_feed_image"
        return media_type
