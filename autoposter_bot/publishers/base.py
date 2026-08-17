from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from autoposter_bot.models import PostJob, Target


@dataclass(slots=True)
class PublishResult:
    platform: str
    destination: str | None
    ok: bool
    detail: str
    external_post_id: str | None = None
    external_url: str | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    # None means this legacy publisher has not been migrated to structured retry
    # semantics yet. The adapter will use its conservative fallback classifier.
    retryable: bool | None = None
    rate_limit_reset_at: datetime | None = None


class Publisher:
    platform: str

    def publish(self, job: PostJob, target: Target, dry_run: bool = False) -> PublishResult:
        raise NotImplementedError
