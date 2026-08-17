from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceRef(StrictModel):
    id: str
    title: str
    url: HttpUrl
    score: float | None = Field(default=None, ge=0, le=1)


class MediaRef(StrictModel):
    asset_id: str = Field(min_length=1)
    type: str = "image"
    url: HttpUrl
    mime_type: str | None = None
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)


class VariantPayload(StrictModel):
    platform: str = Field(min_length=1, max_length=50)
    title: str | None = None
    plain_text: str
    cta: str | None = None
    hashtags: list[str] = Field(default_factory=list)
    blocks: list[dict[str, Any]] = Field(default_factory=list)
    media: list[MediaRef] = Field(default_factory=list)


class CanonicalPayload(StrictModel):
    content_type: str = Field(min_length=1, max_length=50)
    topic: str | None = None
    goal: str | None = None
    title: str
    body: str | None = None
    hook: str | None = None
    cta: str | None = None


class QualityPayload(StrictModel):
    overall: float | None = Field(default=None, ge=0, le=1)
    factuality: float | None = Field(default=None, ge=0, le=1)
    brand_voice: float | None = Field(default=None, ge=0, le=1)
    media: float | None = Field(default=None, ge=0, le=1)


class ContentPackageV1(StrictModel):
    schema_version: Literal["content-package/1.0"]
    content_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    status: Literal["approved"]
    canonical: CanonicalPayload
    variants: list[VariantPayload] = Field(min_length=1)
    sources: list[SourceRef] = Field(default_factory=list)
    quality: QualityPayload


class PerformanceSnapshotRequest(StrictModel):
    event_id: str | None = Field(default=None, max_length=255)
    captured_at: datetime | None = None
    metrics: dict[str, int | float] = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metrics")
    @classmethod
    def validate_metrics(cls, value: dict[str, int | float]) -> dict[str, int | float]:
        cleaned: dict[str, int | float] = {}
        for raw_key, raw_value in value.items():
            key = raw_key.strip().lower()
            if not key:
                continue
            number = float(raw_value)
            if number < 0:
                raise ValueError(f"metric {key} cannot be negative")
            cleaned[key] = int(number) if number.is_integer() else number
        if not cleaned:
            raise ValueError("at least one numeric metric is required")
        return cleaned


def package_hash(package: ContentPackageV1) -> str:
    canonical = json.dumps(package.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
