from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from autoposter_bot.domain.content import PlatformVariant, Publication, PublicationStatus
from autoposter_bot.platforms.base import PublicationResult
from autoposter_bot.platforms.registry import PlatformRegistry


_LEGACY_REMOTE_ID_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "instagram": (
        re.compile(r"Instagram\s+publish\s+succeeded(?:\s*\([^)]*\))?\s*:\s*([^\s]+)", re.IGNORECASE),
    ),
    "tiktok": (
        re.compile(r"TikTok\s+publish\s+initialized\s*:\s*([^\s(]+)", re.IGNORECASE),
    ),
    "vk": (
        re.compile(r"VK\s+publish\s+succeeded\s*:\s*([^\s]+)", re.IGNORECASE),
    ),
}


def _recover_legacy_remote_id(platform: str, result: PublicationResult) -> PublicationResult:
    """Normalize successful legacy adapter details into the canonical remote id."""
    if not result.ok or result.external_post_id:
        return result
    raw = result.raw_response or {}
    detail = raw.get("legacy_detail")
    if not isinstance(detail, str) or not detail.strip():
        return result
    for pattern in _LEGACY_REMOTE_ID_PATTERNS.get(platform.lower(), ()):
        match = pattern.search(detail)
        if match:
            result.external_post_id = match.group(1).strip()
            break
    return result


class PublishingApplication:
    """Application service for one platform-specific publication.

    `attempt_started=True` is used by durable workers after they have persisted
    the `publishing` state and attempt number before the first network call.
    This prevents a process crash from leaving a remote POST behind while the
    database still claims it was safe to retry.
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
        attempt_started: bool = False,
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

        if not dry_run and not attempt_started:
            publication.attempt_count += 1
            publication.status = PublicationStatus.PUBLISHING

        result = adapter.publish(
            variant,
            publication,
            account_options=account_options,
            dry_run=dry_run,
        )
        result = _recover_legacy_remote_id(publication.platform, result)

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
