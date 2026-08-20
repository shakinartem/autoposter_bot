from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from autoposter_bot.platforms.base import PublicationResult


@dataclass(slots=True, frozen=True)
class RetryDecision:
    retry: bool
    next_attempt_at: datetime | None = None
    reason: str = "terminal"


class PublicationRetryPolicy:
    """Bounded retry policy for scheduled publications.

    Only adapters that explicitly mark a failure retryable can enter this path.
    This is intentionally conservative: an unknown outcome after a POST may have
    created the remote post, so blindly retrying every exception can create
    duplicates. Rate-limit reset timestamps override the exponential schedule.
    """

    def __init__(
        self,
        *,
        max_attempts: int = 5,
        base_delay_seconds: int = 60,
        max_delay_seconds: int = 3600,
        jitter_ratio: float = 0.20,
        uniform: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.max_attempts = max(1, max_attempts)
        self.base_delay_seconds = max(1, base_delay_seconds)
        self.max_delay_seconds = max(self.base_delay_seconds, max_delay_seconds)
        self.jitter_ratio = min(max(jitter_ratio, 0.0), 1.0)
        self.uniform = uniform

    def decide(
        self,
        *,
        result: PublicationResult,
        attempt_count: int,
        now: datetime,
    ) -> RetryDecision:
        if result.ok:
            return RetryDecision(False, reason="published")
        if not result.retryable:
            return RetryDecision(False, reason="non_retryable")
        if attempt_count >= self.max_attempts:
            return RetryDecision(False, reason="max_attempts")

        exponent = max(0, attempt_count - 1)
        raw_delay = min(
            self.max_delay_seconds,
            self.base_delay_seconds * (2**exponent),
        )
        jitter_window = raw_delay * self.jitter_ratio
        delay = max(1.0, raw_delay + self.uniform(-jitter_window, jitter_window))
        next_attempt = now + timedelta(seconds=delay)

        if result.rate_limit_reset_at is not None:
            reset_at = result.rate_limit_reset_at
            if reset_at.tzinfo is not None and now.tzinfo is None:
                reset_at = reset_at.replace(tzinfo=None)
            elif reset_at.tzinfo is None and now.tzinfo is not None:
                reset_at = reset_at.replace(tzinfo=now.tzinfo)
            if reset_at > next_attempt:
                # Small positive jitter avoids a worker herd exactly at provider reset.
                reset_jitter = self.uniform(1.0, min(15.0, max(1.0, raw_delay * 0.1)))
                next_attempt = reset_at + timedelta(seconds=reset_jitter)
            return RetryDecision(True, next_attempt, reason="rate_limit")

        return RetryDecision(True, next_attempt, reason="transient")
