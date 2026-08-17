from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class MediaPayload(BaseModel):
    id: str | None = None
    source: str
    media_type: str
    alt_text: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContentCreate(BaseModel):
    title: str = ""
    body: str = ""
    cta: str = ""
    links: list[str] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=list)
    media: list[MediaPayload] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContentUpdate(BaseModel):
    title: str | None = None
    body: str | None = None
    cta: str | None = None
    links: list[str] | None = None
    hashtags: list[str] | None = None
    media: list[MediaPayload] | None = None
    metadata: dict[str, Any] | None = None


class ContentView(BaseModel):
    id: str
    title: str
    body: str
    cta: str
    links: list[str]
    hashtags: list[str]
    media: list[MediaPayload]
    status: str
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class VariantUpsert(BaseModel):
    title: str | None = None
    text: str | None = None
    media: list[MediaPayload] | None = None
    fields: dict[str, Any] | None = None
    sync_with_master: bool | None = None
    metadata: dict[str, Any] | None = None


class VariantView(BaseModel):
    id: str
    content_id: str
    platform: str
    title: str
    text: str
    media: list[MediaPayload]
    fields: dict[str, Any]
    sync_with_master: bool
    revision: int
    metadata: dict[str, Any]


class PublicationCreate(BaseModel):
    account_id: int
    destination: str | None = None
    scheduled_at: datetime | None = None


class PublicationView(BaseModel):
    id: str
    variant_id: str
    platform: str
    account_id: int
    destination: str | None
    scheduled_at: datetime | None
    status: str
    external_post_id: str | None
    external_url: str | None
    published_at: datetime | None
    attempt_count: int
    last_error_code: str | None
    last_error_message: str | None
    metadata: dict[str, Any]


class PublishRequest(BaseModel):
    dry_run: bool = False


class PublishResultView(BaseModel):
    ok: bool
    status: str
    external_post_id: str | None = None
    external_url: str | None = None
    published_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    rate_limit_reset_at: datetime | None = None
    raw_response: dict[str, Any] = Field(default_factory=dict)


class AccountView(BaseModel):
    id: int
    owner_user_id: int | None
    name: str
    platform: str
    destination: str | None
    created_at: str


class WorkspaceView(BaseModel):
    id: int
    name: str
    owner_user_id: int
    created_at: str


class HealthView(BaseModel):
    status: str
    service: str
    version: str
