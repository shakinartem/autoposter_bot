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


@dataclass(slots=True)
class OAuthConnection:
    id: int | None = None
    remote_connection_id: str | None = None
    provider: str = "unknown"
    telegram_user_id: int | None = None
    account_external_id: str | None = None
    account_name: str | None = None
    destination: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str | None = None
    scope: str | None = None
    status: str = "active"
    expires_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    synced_at: str | None = None
    provider_user_id: str | None = None
    link_token: str | None = None
    scopes: str | None = None
    revoked: int = 0

    @property
    def is_active(self) -> bool:
        return self.status.lower() == "active"

    @property
    def connection_key(self) -> str:
        return self.remote_connection_id or ""

    @property
    def platform(self) -> str:
        return self.provider

    @platform.setter
    def platform(self, value: str) -> None:
        self.provider = value

    @property
    def connection_id(self) -> str:
        return self.remote_connection_id or ""

    @connection_id.setter
    def connection_id(self, value: str) -> None:
        self.remote_connection_id = value
