from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from autoposter_bot.models import PostJob, Target


@dataclass(slots=True)
class PublishResult:
    platform: str
    destination: str | None
    ok: bool
    detail: str
    external_post_id: str | None = None
    external_url: str | None = None
    provider_tracking_id: str | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    # None means this legacy publisher has not been migrated to structured retry
    # semantics yet. The adapter will use its conservative fallback classifier.
    retryable: bool | None = None
    rate_limit_reset_at: datetime | None = None
    # Optional canonical lifecycle hint. Legacy publishers omit it and are
    # treated as synchronously published on success.
    status: str | None = None


class Publisher:
    platform: str

    def publish(
        self,
        job: PostJob,
        target: Target,
        dry_run: bool = False,
        *,
        progress_callback: Callable[[PublishResult], None] | None = None,
    ) -> PublishResult:
        raise NotImplementedError

    def fetch_status(self, tracking_id: str, target: Target) -> PublishResult:
        return PublishResult(
            self.platform,
            target.destination,
            False,
            f"{self.platform} publisher does not support status reconciliation",
            provider_tracking_id=tracking_id or None,
            error_code="status_not_supported",
            retryable=False,
            status="unsupported",
        )
