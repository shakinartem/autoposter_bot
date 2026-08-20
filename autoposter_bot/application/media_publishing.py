from __future__ import annotations

from typing import Any

from autoposter_bot.application.publishing import PublishingApplication
from autoposter_bot.domain.content import PlatformVariant, Publication
from autoposter_bot.infrastructure.media_storage import MediaStorage
from autoposter_bot.platforms.base import PublicationResult
from autoposter_bot.platforms.registry import PlatformRegistry


class MediaResolvingPublishingApplication(PublishingApplication):
    """Resolve durable local/S3 media into a source each platform can consume."""

    def __init__(self, registry: PlatformRegistry, media_storage: MediaStorage) -> None:
        super().__init__(registry)
        self.media_storage = media_storage

    def publish(
        self,
        variant: PlatformVariant,
        publication: Publication,
        *,
        account_options: dict[str, Any],
        dry_run: bool = False,
    ) -> PublicationResult:
        with self.media_storage.resolve_variant(variant, publication.platform) as resolved:
            return super().publish(
                resolved,
                publication,
                account_options=account_options,
                dry_run=dry_run,
            )
