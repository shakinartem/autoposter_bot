from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class VideoPostStatus(str, Enum):
    """Status values for video_posts table."""
    DRAFT = "draft"
    READY = "ready"
    SCHEDULED = "scheduled"
    QUEUED = "queued"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    PARTIALLY_FAILED = "partially_failed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class VideoTargetStatus(str, Enum):
    """Status values for video_targets table."""
    PENDING = "pending"
    QUEUED = "queued"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class VideoAttemptStatus(str, Enum):
    """Status values for video_publication_attempts table."""
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRY_SCHEDULED = "retry_scheduled"


class VideoMediaType(str, Enum):
    """Media type values for video_assets table."""
    VIDEO = "video"
    IMAGE = "image"
    THUMBNAIL = "thumbnail"
    COVER = "cover"


@dataclass(slots=True)
class VideoPost:
    """Video post model corresponding to video_posts table."""
    id: int | None = None
    owner_user_id: int | None = None
    external_post_id: str = ""
    title: str | None = None
    text: str = ""
    status: str = VideoPostStatus.DRAFT.value
    scheduled_at: str | None = None
    published_at: str | None = None
    metadata_json: str = "{}"
    created_at: str = ""
    updated_at: str = ""

    @property
    def metadata(self) -> dict[str, Any]:
        import json
        return json.loads(self.metadata_json or "{}")

    @metadata.setter
    def metadata(self, value: dict[str, Any]) -> None:
        import json
        self.metadata_json = json.dumps(value, ensure_ascii=False)


@dataclass(slots=True)
class VideoAsset:
    """Video asset model corresponding to video_assets table."""
    id: int | None = None
    post_id: int = 0
    source: str = ""
    media_type: str = VideoMediaType.VIDEO.value
    order_index: int = 0
    original_filename: str | None = None
    file_size: int | None = None
    mime_type: str | None = None
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    aspect_ratio: str | None = None
    cloudinary_public_id: str | None = None
    cloudinary_url: str | None = None
    processed: int = 0
    options_json: str = "{}"
    created_at: str = ""
    updated_at: str = ""

    @property
    def options(self) -> dict[str, Any]:
        import json
        return json.loads(self.options_json or "{}")

    @options.setter
    def options(self, value: dict[str, Any]) -> None:
        import json
        self.options_json = json.dumps(value, ensure_ascii=False)


@dataclass(slots=True)
class VideoTarget:
    """Video target model corresponding to video_targets table."""
    id: int | None = None
    post_id: int = 0
    account_id: int = 0
    platform: str = ""
    destination: str | None = None
    status: str = VideoTargetStatus.PENDING.value
    options_json: str = "{}"
    created_at: str = ""
    updated_at: str = ""

    @property
    def options(self) -> dict[str, Any]:
        import json
        return json.loads(self.options_json or "{}")

    @options.setter
    def options(self, value: dict[str, Any]) -> None:
        import json
        self.options_json = json.dumps(value, ensure_ascii=False)


@dataclass(slots=True)
class VideoPublicationAttempt:
    """Video publication attempt model corresponding to video_publication_attempts table."""
    id: int | None = None
    post_id: int = 0
    target_id: int = 0
    platform: str = ""
    attempt_number: int = 0
    status: str = VideoAttemptStatus.STARTED.value
    external_id: str | None = None
    error_code: str | None = None
    error_detail: str | None = None
    retry_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    created_at: str = ""
    updated_at: str = ""