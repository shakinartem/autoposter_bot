from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from autoposter_bot.video_models import (
    VideoAsset,
    VideoMediaType,
    VideoPost,
    VideoPostStatus,
    VideoTarget,
    VideoTargetStatus,
)


class ValidationSeverity(str, Enum):
    """Severity level for a validation issue."""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(slots=True)
class ValidationIssue:
    """A single validation issue found during check."""
    code: str
    message: str
    severity: ValidationSeverity = ValidationSeverity.ERROR
    field: str | None = None
    platform: str | None = None
    details: dict[str, Any] | None = None


@dataclass(slots=True)
class ValidationResult:
    """Aggregated result of a validation run."""
    ok: bool = True
    issues: list[ValidationIssue] = field(default_factory=list)

    def has_errors(self) -> bool:
        return any(i.severity == ValidationSeverity.ERROR for i in self.issues)

    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == ValidationSeverity.ERROR]

    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == ValidationSeverity.WARNING]

    def infos(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == ValidationSeverity.INFO]


@dataclass(slots=True)
class PlatformVideoProfile:
    """Describes known requirements/limits for a video platform.

    Fields with None mean the limit is unknown or not applicable.
    Verify with official platform docs before relying on a non-None value.
    """
    platform: str
    required_media_type: str | None = VideoMediaType.VIDEO.value
    allowed_mime_types: list[str] | None = None
    allowed_file_extensions: list[str] | None = None
    min_duration_seconds: float | None = None
    max_duration_seconds: float | None = None
    min_width: int | None = None
    min_height: int | None = None
    max_width: int | None = None
    max_height: int | None = None
    max_file_size_bytes: int | None = None
    caption_max_length: int | None = None
    title_max_length: int | None = None
    requires_account_id: bool = True
    requires_cloudinary_url: bool = False
    notes: str | None = None


# ---------------------------------------------------------------------------
# Default platform profiles (cautious, non-authoritative values)
# ---------------------------------------------------------------------------

PLATFORM_PROFILES: dict[str, PlatformVideoProfile] = {
    "tiktok": PlatformVideoProfile(
        platform="tiktok",
        required_media_type=VideoMediaType.VIDEO.value,
        allowed_mime_types=["video/mp4", "video/quicktime", "video/webm", "video/x-msvideo"],
        allowed_file_extensions=[".mp4", ".mov", ".webm", ".avi"],
        min_duration_seconds=None,
        max_duration_seconds=180.0,
        min_width=None,
        min_height=None,
        max_width=3840,
        max_height=3840,
        max_file_size_bytes=524_288_000,  # 500 MB
        caption_max_length=2200,
        title_max_length=None,
        requires_account_id=True,
        requires_cloudinary_url=False,
        notes="TODO: verify min_duration, min_width, min_height with official TikTok API docs",
    ),
    "instagram": PlatformVideoProfile(
        platform="instagram",
        required_media_type=VideoMediaType.VIDEO.value,
        allowed_mime_types=["video/mp4"],
        allowed_file_extensions=[".mp4"],
        min_duration_seconds=None,
        max_duration_seconds=90.0,
        min_width=None,
        min_height=None,
        max_width=None,
        max_height=None,
        max_file_size_bytes=None,  # TODO: ~100 MB observed but unconfirmed
        caption_max_length=2200,
        title_max_length=None,
        requires_account_id=True,
        requires_cloudinary_url=True,
        notes="TODO: verify min_duration, max_file_size, min_width, min_height with Meta Graph API docs",
    ),
    "youtube": PlatformVideoProfile(
        platform="youtube",
        required_media_type=VideoMediaType.VIDEO.value,
        allowed_mime_types=["video/mp4", "video/quicktime", "video/x-msvideo",
                            "video/x-ms-wmv", "video/x-flv", "video/x-matroska",
                            "video/webm"],
        allowed_file_extensions=[".mp4", ".mov", ".avi", ".wmv", ".flv", ".mkv", ".webm"],
        min_duration_seconds=None,
        max_duration_seconds=60.0,  # Shorts limit
        min_width=None,
        min_height=None,
        max_width=None,
        max_height=None,
        max_file_size_bytes=None,
        caption_max_length=5000,
        title_max_length=100,
        requires_account_id=True,
        requires_cloudinary_url=False,
        notes="TODO: verify min_duration, dimensions with YouTube Data API docs",
    ),
}

_SUPPORTED_PLATFORMS = frozenset(PLATFORM_PROFILES.keys())

_VALID_POST_STATUSES = frozenset(v.value for v in VideoPostStatus)
_VALID_TARGET_STATUSES = frozenset(v.value for v in VideoTargetStatus)
_VALID_MEDIA_TYPES = frozenset(v.value for v in VideoMediaType)


class VideoPostValidationService:
    """Validates the structure and metadata of a video post before publication.

    This service is stateless and isolated — it does NOT access storage,
    does NOT call publishers, and does NOT modify any data.
    """

    def __init__(self, profiles: dict[str, PlatformVideoProfile] | None = None) -> None:
        self._profiles = dict(profiles) if profiles is not None else dict(PLATFORM_PROFILES)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate_post(
        self,
        post: VideoPost,
        assets: list[VideoAsset],
        targets: list[VideoTarget],
    ) -> ValidationResult:
        """Run full validation across all targets for a video post."""
        result = ValidationResult()
        self._check_post(result, post)
        self._check_assets_exist(result, assets)
        self._check_targets_exist(result, targets)

        if not result.has_errors():
            self._check_has_primary_media(result, assets)

        for target in targets:
            platform_result = self.validate_for_platform(post, assets, target)
            result.issues.extend(platform_result.issues)
            if platform_result.has_errors():
                result.ok = False

        if result.has_errors():
            result.ok = False
        return result

    def validate_for_platform(
        self,
        post: VideoPost,
        assets: list[VideoAsset],
        target: VideoTarget,
    ) -> ValidationResult:
        """Run validation scoped to a single target/platform."""
        result = ValidationResult()

        profile = self._get_profile(target.platform, result)
        if profile is None:
            result.ok = False
            return result

        self._check_target_status(result, target)
        self._check_target_account_id(result, target, profile)
        self._check_target_post_id(result, target, post)

        for asset in assets:
            self._check_asset_source(result, asset, target.platform)
            self._check_asset_media_type(result, asset, profile, target.platform)
            self._check_asset_mime_type(result, asset, profile, target.platform)
            self._check_asset_extension(result, asset, profile, target.platform)
            self._check_asset_duration(result, asset, profile, target.platform)
            self._check_asset_dimensions(result, asset, profile, target.platform)
            self._check_asset_file_size(result, asset, profile, target.platform)
            self._check_asset_cloudinary(result, asset, profile, target.platform)

        self._check_caption_length(result, post, profile, target.platform)
        self._check_title_length(result, post, profile, target.platform)

        if result.has_errors():
            result.ok = False

        return result

    # ------------------------------------------------------------------
    # Post-level checks
    # ------------------------------------------------------------------

    def _check_post(self, result: ValidationResult, post: VideoPost) -> None:
        if post.id is None or post.id < 1:
            result.issues.append(ValidationIssue(
                code="POST_NO_ID",
                message="Video post has no valid id",
                severity=ValidationSeverity.ERROR,
                field="id",
            ))
        if post.status not in _VALID_POST_STATUSES:
            result.issues.append(ValidationIssue(
                code="POST_INVALID_STATUS",
                message=f"Unknown post status: {post.status!r}",
                severity=ValidationSeverity.ERROR,
                field="status",
            ))

    def _check_assets_exist(self, result: ValidationResult, assets: list[VideoAsset]) -> None:
        if not assets:
            result.issues.append(ValidationIssue(
                code="POST_NO_ASSETS",
                message="Video post has no assets",
                severity=ValidationSeverity.ERROR,
            ))

    def _check_targets_exist(self, result: ValidationResult, targets: list[VideoTarget]) -> None:
        if not targets:
            result.issues.append(ValidationIssue(
                code="POST_NO_TARGETS",
                message="Video post has no targets",
                severity=ValidationSeverity.ERROR,
            ))

    def _check_has_primary_media(self, result: ValidationResult, assets: list[VideoAsset]) -> None:
        has_video = any(a.media_type == VideoMediaType.VIDEO.value for a in assets)
        if not has_video:
            result.issues.append(ValidationIssue(
                code="POST_NO_VIDEO_ASSET",
                message="No asset with media_type='video' found; at least one primary video asset is expected",
                severity=ValidationSeverity.ERROR,
            ))

    # ------------------------------------------------------------------
    # Target-level checks
    # ------------------------------------------------------------------

    def _get_profile(self, platform: str, result: ValidationResult) -> PlatformVideoProfile | None:
        if platform not in self._profiles:
            result.issues.append(ValidationIssue(
                code="TARGET_UNSUPPORTED_PLATFORM",
                message=f"Platform {platform!r} is not supported. Supported: {sorted(self._profiles.keys())}",
                severity=ValidationSeverity.ERROR,
                field="platform",
                platform=platform,
            ))
            return None
        return self._profiles[platform]

    def _check_target_status(self, result: ValidationResult, target: VideoTarget) -> None:
        if target.status not in _VALID_TARGET_STATUSES:
            result.issues.append(ValidationIssue(
                code="TARGET_INVALID_STATUS",
                message=f"Unknown target status: {target.status!r}",
                severity=ValidationSeverity.ERROR,
                field="status",
                platform=target.platform,
            ))

    def _check_target_account_id(self, result: ValidationResult, target: VideoTarget,
                                 profile: PlatformVideoProfile) -> None:
        if profile.requires_account_id and (target.account_id is None or target.account_id < 1):
            result.issues.append(ValidationIssue(
                code="TARGET_NO_ACCOUNT_ID",
                message=f"Target on {target.platform!r} requires a valid account_id",
                severity=ValidationSeverity.ERROR,
                field="account_id",
                platform=target.platform,
            ))

    def _check_target_post_id(self, result: ValidationResult, target: VideoTarget,
                              post: VideoPost) -> None:
        if post.id is not None and target.post_id != post.id:
            result.issues.append(ValidationIssue(
                code="TARGET_POST_ID_MISMATCH",
                message=f"Target post_id={target.post_id} does not match post id={post.id}",
                severity=ValidationSeverity.ERROR,
                field="post_id",
                platform=target.platform,
            ))

    # ------------------------------------------------------------------
    # Asset-level checks
    # ------------------------------------------------------------------

    def _check_asset_source(self, result: ValidationResult, asset: VideoAsset,
                            platform: str) -> None:
        if not asset.source and not asset.cloudinary_url:
            result.issues.append(ValidationIssue(
                code="ASSET_NO_SOURCE",
                message="Asset has no source (source=empty, cloudinary_url=empty)",
                severity=ValidationSeverity.ERROR,
                field="source",
                platform=platform,
            ))

    def _check_asset_media_type(self, result: ValidationResult, asset: VideoAsset,
                                profile: PlatformVideoProfile, platform: str) -> None:
        if asset.media_type not in _VALID_MEDIA_TYPES:
            result.issues.append(ValidationIssue(
                code="ASSET_INVALID_MEDIA_TYPE",
                message=f"Unknown media_type: {asset.media_type!r}",
                severity=ValidationSeverity.ERROR,
                field="media_type",
                platform=platform,
            ))
            return
        if profile.required_media_type and asset.media_type != profile.required_media_type:
            # Only warn — images/thumbnails may accompany the primary video
            result.issues.append(ValidationIssue(
                code="ASSET_UNEXPECTED_MEDIA_TYPE",
                message=f"Asset media_type={asset.media_type!r} differs from expected "
                        f"{profile.required_media_type!r} for {profile.platform}",
                severity=ValidationSeverity.WARNING,
                field="media_type",
                platform=platform,
            ))

    def _check_asset_mime_type(self, result: ValidationResult, asset: VideoAsset,
                               profile: PlatformVideoProfile, platform: str) -> None:
        if not asset.mime_type or not profile.allowed_mime_types:
            return
        if asset.mime_type not in profile.allowed_mime_types:
            result.issues.append(ValidationIssue(
                code="ASSET_MIME_TYPE_NOT_ALLOWED",
                message=f"MIME type {asset.mime_type!r} is not in allowed list "
                        f"for {platform}: {profile.allowed_mime_types}",
                severity=ValidationSeverity.WARNING,
                field="mime_type",
                platform=platform,
            ))

    def _check_asset_extension(self, result: ValidationResult, asset: VideoAsset,
                               profile: PlatformVideoProfile, platform: str) -> None:
        if not asset.original_filename or not profile.allowed_file_extensions:
            return
        ext = Path(asset.original_filename).suffix.lower()
        if ext and ext not in profile.allowed_file_extensions:
            result.issues.append(ValidationIssue(
                code="ASSET_EXTENSION_NOT_ALLOWED",
                message=f"File extension {ext!r} is not in allowed list "
                        f"for {platform}: {profile.allowed_file_extensions}",
                severity=ValidationSeverity.WARNING,
                field="original_filename",
                platform=platform,
            ))

    def _check_asset_duration(self, result: ValidationResult, asset: VideoAsset,
                              profile: PlatformVideoProfile, platform: str) -> None:
        if asset.duration_seconds is None:
            return
        if profile.min_duration_seconds is not None and asset.duration_seconds < profile.min_duration_seconds:
            result.issues.append(ValidationIssue(
                code="ASSET_DURATION_TOO_SHORT",
                message=f"Duration {asset.duration_seconds}s is below minimum {profile.min_duration_seconds}s "
                        f"for {platform}",
                severity=ValidationSeverity.ERROR,
                field="duration_seconds",
                platform=platform,
                details={"duration": asset.duration_seconds, "min": profile.min_duration_seconds},
            ))
        if profile.max_duration_seconds is not None and asset.duration_seconds > profile.max_duration_seconds:
            result.issues.append(ValidationIssue(
                code="ASSET_DURATION_TOO_LONG",
                message=f"Duration {asset.duration_seconds}s exceeds maximum {profile.max_duration_seconds}s "
                        f"for {platform}",
                severity=ValidationSeverity.ERROR,
                field="duration_seconds",
                platform=platform,
                details={"duration": asset.duration_seconds, "max": profile.max_duration_seconds},
            ))

    def _check_asset_dimensions(self, result: ValidationResult, asset: VideoAsset,
                                profile: PlatformVideoProfile, platform: str) -> None:
        if asset.width is None or asset.height is None:
            return
        if profile.min_width is not None and asset.width < profile.min_width:
            result.issues.append(ValidationIssue(
                code="ASSET_WIDTH_TOO_SMALL",
                message=f"Width {asset.width}px is below minimum {profile.min_width}px for {platform}",
                severity=ValidationSeverity.WARNING,
                field="width",
                platform=platform,
            ))
        if profile.max_width is not None and asset.width > profile.max_width:
            result.issues.append(ValidationIssue(
                code="ASSET_WIDTH_TOO_LARGE",
                message=f"Width {asset.width}px exceeds maximum {profile.max_width}px for {platform}",
                severity=ValidationSeverity.WARNING,
                field="width",
                platform=platform,
            ))
        if profile.min_height is not None and asset.height < profile.min_height:
            result.issues.append(ValidationIssue(
                code="ASSET_HEIGHT_TOO_SMALL",
                message=f"Height {asset.height}px is below minimum {profile.min_height}px for {platform}",
                severity=ValidationSeverity.WARNING,
                field="height",
                platform=platform,
            ))
        if profile.max_height is not None and asset.height > profile.max_height:
            result.issues.append(ValidationIssue(
                code="ASSET_HEIGHT_TOO_LARGE",
                message=f"Height {asset.height}px exceeds maximum {profile.max_height}px for {platform}",
                severity=ValidationSeverity.WARNING,
                field="height",
                platform=platform,
            ))

    def _check_asset_file_size(self, result: ValidationResult, asset: VideoAsset,
                               profile: PlatformVideoProfile, platform: str) -> None:
        if asset.file_size is None or profile.max_file_size_bytes is None:
            return
        if asset.file_size > profile.max_file_size_bytes:
            result.issues.append(ValidationIssue(
                code="ASSET_FILE_SIZE_TOO_LARGE",
                message=f"File size {asset.file_size} bytes exceeds maximum "
                        f"{profile.max_file_size_bytes} bytes for {platform}",
                severity=ValidationSeverity.ERROR,
                field="file_size",
                platform=platform,
                details={"file_size": asset.file_size, "max": profile.max_file_size_bytes},
            ))

    def _check_asset_cloudinary(self, result: ValidationResult, asset: VideoAsset,
                                profile: PlatformVideoProfile, platform: str) -> None:
        if not profile.requires_cloudinary_url:
            return
        if not asset.cloudinary_url:
            result.issues.append(ValidationIssue(
                code="ASSET_NO_CLOUDINARY_URL",
                message=f"Platform {platform} requires a cloudinary_url but asset has none",
                severity=ValidationSeverity.ERROR,
                field="cloudinary_url",
                platform=platform,
            ))

    # ------------------------------------------------------------------
    # Caption / title checks
    # ------------------------------------------------------------------

    def _check_caption_length(self, result: ValidationResult, post: VideoPost,
                              profile: PlatformVideoProfile, platform: str) -> None:
        if profile.caption_max_length is None:
            return
        caption = post.text or ""
        if len(caption) > profile.caption_max_length:
            result.issues.append(ValidationIssue(
                code="POST_CAPTION_TOO_LONG",
                message=f"Caption length {len(caption)} exceeds maximum {profile.caption_max_length} "
                        f"for {platform}",
                severity=ValidationSeverity.ERROR,
                field="text",
                platform=platform,
                details={"length": len(caption), "max": profile.caption_max_length},
            ))

    def _check_title_length(self, result: ValidationResult, post: VideoPost,
                            profile: PlatformVideoProfile, platform: str) -> None:
        if profile.title_max_length is None:
            return
        title = post.title or ""
        if len(title) > profile.title_max_length:
            result.issues.append(ValidationIssue(
                code="POST_TITLE_TOO_LONG",
                message=f"Title length {len(title)} exceeds maximum {profile.title_max_length} "
                        f"for {platform}",
                severity=ValidationSeverity.ERROR,
                field="title",
                platform=platform,
                details={"length": len(title), "max": profile.title_max_length},
            ))