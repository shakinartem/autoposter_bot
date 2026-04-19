from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class MediaItem:
    source: str
    media_type: str
    order_index: int = 0
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def is_remote(self) -> bool:
        return self.source.startswith("http://") or self.source.startswith("https://")


@dataclass(slots=True)
class Target:
    platform: str
    destination: str | None = None
    account_id: int | None = None
    account_name: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PostJob:
    post_id: str
    content_type: str
    text: str
    media_items: list[MediaItem] = field(default_factory=list)
    scheduled_at: datetime | None = None
    targets: list[Target] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def primary_media(self) -> str | None:
        if not self.media_items:
            return None
        return self.media_items[0].source
