from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from autoposter_bot.domain.content import PlatformVariant, Publication


@dataclass(slots=True)
class CapabilitySpec:
    platform: str
    content_types: tuple[str, ...]
    fields: dict[str, dict[str, Any]] = field(default_factory=dict)
    features: dict[str, bool] = field(default_factory=dict)
    limits: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class ValidationIssue:
    field: str
    message: str
    code: str = "invalid"


@dataclass(slots=True)
class PublicationResult:
    ok: bool
    status: str
    external_post_id: str | None = None
    external_url: str | None = None
    provider_tracking_id: str | None = None
    published_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    rate_limit_reset_at: datetime | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)


class PlatformAdapter(ABC):
    platform: str

    @abstractmethod
    def capabilities(self) -> CapabilitySpec:
        raise NotImplementedError

    @abstractmethod
    def validate(self, variant: PlatformVariant) -> list[ValidationIssue]:
        raise NotImplementedError

    @abstractmethod
    def publish(
        self,
        variant: PlatformVariant,
        publication: Publication,
        *,
        account_options: dict[str, Any],
        dry_run: bool = False,
        progress_callback: Callable[[PublicationResult], None] | None = None,
    ) -> PublicationResult:
        raise NotImplementedError

    def fetch_status(
        self,
        publication: Publication,
        *,
        account_options: dict[str, Any],
    ) -> PublicationResult:
        return PublicationResult(
            ok=False,
            status="unsupported",
            provider_tracking_id=publication.provider_tracking_id,
            error_code="status_not_supported",
            error_message=f"{self.platform} adapter does not support status reconciliation",
        )

    def update(
        self,
        variant: PlatformVariant,
        publication: Publication,
        *,
        account_options: dict[str, Any],
    ) -> PublicationResult:
        return PublicationResult(
            ok=False,
            status="unsupported",
            error_code="update_not_supported",
            error_message=f"{self.platform} adapter does not support updates",
        )

    def delete(
        self,
        publication: Publication,
        *,
        account_options: dict[str, Any],
    ) -> PublicationResult:
        return PublicationResult(
            ok=False,
            status="unsupported",
            error_code="delete_not_supported",
            error_message=f"{self.platform} adapter does not support deletion",
        )

    def fetch_metrics(
        self,
        publication: Publication,
        *,
        account_options: dict[str, Any],
    ) -> dict[str, Any]:
        return {}
