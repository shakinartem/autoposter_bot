from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable

from autoposter_bot.domain.content import PlatformVariant, Publication, PublicationStatus
from autoposter_bot.platforms.base import PublicationResult
from autoposter_bot.platforms.registry import PlatformRegistry


_LEGACY_REMOTE_ID_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "instagram": (
        re.compile(r"Instagram\s+publish\s+succeeded(?:\s*\([^)]*\))?\s*:\s*([^\s]+)", re.IGNORECASE),
    ),
    "vk": (
        re.compile(r"VK\s+publish\s+succeeded\s*:\s*([^\s]+)", re.IGNORECASE),
    ),
}
_LEGACY_TRACKING_ID_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "tiktok": (
        re.compile(r"TikTok\s+publish\s+initialized\s*:\s*([^\s(]+)", re.IGNORECASE),
    ),
}


def _recover_legacy_identifiers(platform: str, result: PublicationResult) -> PublicationResult:
    """Recover old string-only IDs while publishers migrate to structured fields.

    TikTok publish_id is intentionally a provider tracking identity, not a final
    post id. Keeping those concepts separate prevents analytics from querying a
    processing handle as though it were a published video id.
    """
    if not result.ok:
        return result
    raw = result.raw_response or {}
    detail = raw.get("legacy_detail")
    if not isinstance(detail, str) or not detail.strip():
        return result
    if not result.external_post_id:
        for pattern in _LEGACY_REMOTE_ID_PATTERNS.get(platform.lower(), ()):
            match = pattern.search(detail)
            if match:
                result.external_post_id = match.group(1).strip()
                break
    if not result.provider_tracking_id:
        for pattern in _LEGACY_TRACKING_ID_PATTERNS.get(platform.lower(), ()):
            match = pattern.search(detail)
            if match:
                result.provider_tracking_id = match.group(1).strip()
                if platform.lower() == "tiktok" and result.status == "published":
                    result.status = "processing"
                break
    return result


class PublishingApplication:
    """Application service for publish and provider-status lifecycle transitions."""

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
        progress_callback: Callable[[PublicationResult], None] | None = None,
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

        publish_kwargs: dict[str, Any] = {
            "account_options": account_options,
            "dry_run": dry_run,
        }
        if progress_callback is not None:
            publish_kwargs["progress_callback"] = progress_callback
        result = adapter.publish(variant, publication, **publish_kwargs)
        result = _recover_legacy_identifiers(publication.platform, result)

        if dry_run:
            return result

        self._apply_result(publication, result, from_reconciliation=False)
        return result

    def reconcile(
        self,
        publication: Publication,
        *,
        account_options: dict[str, Any],
    ) -> PublicationResult:
        adapter = self.registry.get(publication.platform)
        result = adapter.fetch_status(publication, account_options=account_options)
        result = _recover_legacy_identifiers(publication.platform, result)
        self._apply_result(publication, result, from_reconciliation=True)
        return result

    @staticmethod
    def _apply_result(
        publication: Publication,
        result: PublicationResult,
        *,
        from_reconciliation: bool,
    ) -> None:
        if result.provider_tracking_id:
            publication.provider_tracking_id = result.provider_tracking_id
        if result.external_post_id:
            publication.external_post_id = result.external_post_id
        if result.external_url:
            publication.external_url = result.external_url

        if result.ok and result.status == "processing":
            publication.status = PublicationStatus.PROCESSING
            publication.published_at = None
            publication.last_error_code = None
            publication.last_error_message = None
            publication.metadata.setdefault("processing_started_at", datetime.now().isoformat())
            return

        if result.ok:
            publication.status = PublicationStatus.PUBLISHED
            publication.published_at = result.published_at or datetime.now()
            publication.last_error_code = None
            publication.last_error_message = None
            return

        if from_reconciliation and result.status not in {"failed", "invalid"}:
            # Status polling itself failed. The original remote publication may
            # still complete, so do not convert a processing item into a failed
            # publish or accidentally make it eligible for republishing.
            publication.metadata["reconciliation_last_error"] = {
                "code": result.error_code,
                "message": result.error_message,
                "checked_at": datetime.now().isoformat(),
            }
            return

        if not from_reconciliation and publication.provider_tracking_id:
            # The provider already issued a durable async tracking handle. A later
            # upload/transport failure is not permission to create a fresh post.
            # Keep it in reconciliation until provider status is authoritative.
            publication.status = PublicationStatus.PROCESSING
            publication.metadata["post_init_error"] = {
                "code": result.error_code,
                "message": result.error_message,
                "recorded_at": datetime.now().isoformat(),
            }
            publication.last_error_code = result.error_code
            publication.last_error_message = result.error_message
            return

        publication.status = PublicationStatus.FAILED
        publication.last_error_code = result.error_code
        publication.last_error_message = result.error_message
