from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


class ContentStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    ARCHIVED = "archived"


class PublicationStatus(StrEnum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    QUEUED = "queued"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class MediaAsset:
    source: str
    media_type: str
    id: str = field(default_factory=lambda: str(uuid4()))
    alt_text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ContentItem:
    title: str
    body: str
    id: str = field(default_factory=lambda: str(uuid4()))
    cta: str = ""
    links: list[str] = field(default_factory=list)
    hashtags: list[str] = field(default_factory=list)
    media: list[MediaAsset] = field(default_factory=list)
    status: ContentStatus = ContentStatus.DRAFT
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass(slots=True)
class PlatformVariant:
    content_id: str
    platform: str
    text: str
    id: str = field(default_factory=lambda: str(uuid4()))
    title: str = ""
    media: list[MediaAsset] = field(default_factory=list)
    fields: dict[str, Any] = field(default_factory=dict)
    sync_with_master: bool = True
    revision: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    def override(self) -> None:
        self.sync_with_master = False
        self.revision += 1


@dataclass(slots=True)
class Publication:
    variant_id: str
    platform: str
    account_id: int
    id: str = field(default_factory=lambda: str(uuid4()))
    destination: str | None = None
    # The user's intended schedule. Never rewrite this when retrying because it
    # is part of the publication-performance dataset and lateness measurement.
    scheduled_at: datetime | None = None
    # Internal delivery schedule for retries. None means use scheduled_at.
    next_attempt_at: datetime | None = None
    status: PublicationStatus = PublicationStatus.DRAFT
    external_post_id: str | None = None
    external_url: str | None = None
    published_at: datetime | None = None
    attempt_count: int = 0
    last_error_code: str | None = None
    last_error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
