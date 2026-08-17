from __future__ import annotations

from datetime import datetime
from typing import Any

from autoposter_bot.domain.content import PlatformVariant, Publication, PublicationStatus
from autoposter_bot.platforms.base import PublicationResult
from autoposter_bot.platforms.registry import PlatformRegistry


class PublishingApplication:
    """Application service for one platform-specific publication.

    Publication lifecycle belongs here instead of inside Telegram UI or a scheduler.
    Web, bot, API and workers can all call the same method.
    """

    def __init__(self, registry: PlatformRegistry) -> None:
        self.registry = registry

    def publish(
        self,
        variant: PlatformVariant,
        publication: Publication,
        *,
        account_options: dict[str, Any],
        dry_run: bool = False,
    ) -> PublicationResult:
        if variant.id != publication.variant_id:
            return PublicationResult(
                ok=False,
                status="invalid",
                error_code="variant_mismatch",
                error_message="Publication references a different platform variant",
            )
        if variant.platform.lower() != publication.platform.lower():
            return PublicationResult(
                ok=False,
                status="invalid",
                error_code="platform_mismatch",
                error_message="Publication platform does not match variant platform",
            )

        adapter = self.registry.get(publication.platform)
        issues = adapter.validate(variant)
        if issues:
            if not dry_run:
                publication.status = PublicationStatus.FAILED
                publication.last_error_code = issues[0].code
                publication.last_error_message = issues[0].message
            return PublicationResult(
                ok=False,
                status="invalid",
                error_code=issues[0].code,
                error_message=issues[0].message,
            )

        if not dry_run:
            publication.attempt_count += 1
            publication.status = PublicationStatus.PUBLISHING

        result = adapter.publish(
            variant,
            publication,
            account_options=account_options,
            dry_run=dry_run,
        )

        if dry_run:
            return result

        if result.ok:
            publication.status = PublicationStatus.PUBLISHED
            publication.external_post_id = result.external_post_id
            publication.external_url = result.external_url
            publication.published_at = result.published_at or datetime.now()
            publication.last_error_code = None
            publication.last_error_message = None
        else:
            publication.status = PublicationStatus.FAILED
            publication.last_error_code = result.error_code
            publication.last_error_message = result.error_message
        return result
