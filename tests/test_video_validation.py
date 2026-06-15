from __future__ import annotations

import pytest

from autoposter_bot.video_models import VideoAsset, VideoPost, VideoTarget, VideoMediaType
from autoposter_bot.video_validation import (
    ValidationSeverity,
    VideoPostValidationService,
    PlatformVideoProfile,
)


@pytest.fixture
def service() -> VideoPostValidationService:
    return VideoPostValidationService()


def _make_post(
    post_id: int = 1,
    text: str = "Hello video world",
    status: str = "draft",
    title: str | None = "My Video",
) -> VideoPost:
    return VideoPost(
        id=post_id,
        external_post_id="ext-001",
        text=text,
        title=title,
        status=status,
    )


def _make_asset(
    source: str = "https://example.com/video.mp4",
    media_type: str = VideoMediaType.VIDEO.value,
    mime_type: str | None = "video/mp4",
    original_filename: str | None = "video.mp4",
    duration_seconds: float | None = 30.0,
    width: int | None = 1920,
    height: int | None = 1080,
    file_size: int | None = 10_000_000,
    cloudinary_url: str | None = None,
) -> VideoAsset:
    return VideoAsset(
        post_id=1,
        source=source,
        media_type=media_type,
        order_index=0,
        original_filename=original_filename,
        mime_type=mime_type,
        duration_seconds=duration_seconds,
        width=width,
        height=height,
        file_size=file_size,
        cloudinary_url=cloudinary_url,
    )


def _make_target(
    platform: str = "tiktok",
    account_id: int = 100,
    post_id: int = 1,
    status: str = "pending",
) -> VideoTarget:
    return VideoTarget(
        post_id=post_id,
        account_id=account_id,
        platform=platform,
        status=status,
    )


class TestValidationResultHelpers:
    def test_empty_result_is_ok(self) -> None:
        from autoposter_bot.video_validation import ValidationResult
        r = ValidationResult()
        assert r.ok is True
        assert r.has_errors() is False
        assert r.errors() == []
        assert r.warnings() == []
        assert r.infos() == []

    def test_result_with_errors(self) -> None:
        from autoposter_bot.video_validation import ValidationResult, ValidationIssue, ValidationSeverity
        r = ValidationResult()
        r.issues.append(ValidationIssue(code="TEST", message="test error", severity=ValidationSeverity.ERROR))
        assert r.ok is True  # unchanged until explicitly set
        assert r.has_errors() is True
        assert len(r.errors()) == 1
        assert len(r.warnings()) == 0


class TestValidatePost:
    def test_valid_minimal_post(self, service: VideoPostValidationService) -> None:
        post = _make_post()
        assets = [_make_asset()]
        targets = [_make_target()]
        result = service.validate_post(post, assets, targets)
        assert result.ok is True, f"Expected OK, got issues: {result.issues}"
        assert not result.has_errors()

    def test_post_without_assets_returns_error(self, service: VideoPostValidationService) -> None:
        post = _make_post()
        result = service.validate_post(post, [], [_make_target()])
        assert result.ok is False
        assert result.has_errors()
        codes = {i.code for i in result.issues}
        assert "POST_NO_ASSETS" in codes

    def test_post_without_targets_returns_error(self, service: VideoPostValidationService) -> None:
        post = _make_post()
        assets = [_make_asset()]
        result = service.validate_post(post, assets, [])
        assert result.ok is False
        assert result.has_errors()
        codes = {i.code for i in result.issues}
        assert "POST_NO_TARGETS" in codes

    def test_post_without_primary_video_returns_error(self, service: VideoPostValidationService) -> None:
        post = _make_post()
        assets = [_make_asset(
            source="https://example.com/thumb.jpg",
            media_type=VideoMediaType.THUMBNAIL.value,
            mime_type="image/jpeg",
            original_filename="thumb.jpg",
            duration_seconds=None,
            width=None,
            height=None,
            file_size=None,
        )]
        targets = [_make_target()]
        result = service.validate_post(post, assets, targets)
        assert result.ok is False
        codes = {i.code for i in result.issues}
        assert "POST_NO_VIDEO_ASSET" in codes

    def test_unsupported_platform_returns_error(self, service: VideoPostValidationService) -> None:
        post = _make_post()
        assets = [_make_asset()]
        targets = [_make_target(platform="snapchat")]
        result = service.validate_post(post, assets, targets)
        assert result.ok is False
        codes = {i.code for i in result.issues}
        assert "TARGET_UNSUPPORTED_PLATFORM" in codes

    def test_post_id_none_returns_error(self, service: VideoPostValidationService) -> None:
        post = _make_post(post_id=None)
        assets = [_make_asset()]
        targets = [_make_target()]
        result = service.validate_post(post, assets, targets)
        assert result.ok is False
        codes = {i.code for i in result.issues}
        assert "POST_NO_ID" in codes

    def test_post_invalid_status(self, service: VideoPostValidationService) -> None:
        post = _make_post(status="invalid_status")
        assets = [_make_asset()]
        targets = [_make_target()]
        result = service.validate_post(post, assets, targets)
        assert result.ok is False
        codes = {i.code for i in result.issues}
        assert "POST_INVALID_STATUS" in codes

    def test_unknown_metadata_does_not_break(self, service: VideoPostValidationService) -> None:
        post = _make_post()
        post.metadata = {"custom_field": "some_value", "extra": [1, 2, 3]}
        assets = [_make_asset()]
        targets = [_make_target()]
        result = service.validate_post(post, assets, targets)
        assert result.ok is True


class TestValidateForPlatform:
    def test_target_without_account_id_on_required_platform(self, service: VideoPostValidationService) -> None:
        post = _make_post()
        assets = [_make_asset()]
        target = _make_target(account_id=0)
        result = service.validate_for_platform(post, assets, target)
        assert result.ok is False
        codes = {i.code for i in result.issues}
        assert "TARGET_NO_ACCOUNT_ID" in codes

    def test_asset_without_source_returns_error(self, service: VideoPostValidationService) -> None:
        post = _make_post()
        assets = [_make_asset(source="")]
        targets = [_make_target()]
        result = service.validate_post(post, assets, targets)
        assert result.ok is False
        codes = {i.code for i in result.issues}
        assert "ASSET_NO_SOURCE" in codes

    def test_asset_invalid_media_type(self, service: VideoPostValidationService) -> None:
        post = _make_post()
        assets = [_make_asset(media_type="audio")]
        targets = [_make_target()]
        result = service.validate_post(post, assets, targets)
        assert result.ok is False
        codes = {i.code for i in result.issues}
        assert "ASSET_INVALID_MEDIA_TYPE" in codes

    def test_asset_unexpected_media_type_is_warning(self, service: VideoPostValidationService) -> None:
        post = _make_post()
        # primary is video
        video_asset = _make_asset()
        # extra is image
        image_asset = _make_asset(
            source="https://example.com/cover.jpg",
            media_type=VideoMediaType.IMAGE.value,
            mime_type="image/jpeg",
            original_filename="cover.jpg",
            duration_seconds=None,
            width=None,
            height=None,
            file_size=None,
        )
        assets = [video_asset, image_asset]
        targets = [_make_target()]
        result = service.validate_post(post, assets, targets)
        warnings = result.warnings()
        warning_codes = {w.code for w in warnings}
        assert "ASSET_UNEXPECTED_MEDIA_TYPE" in warning_codes, (
            f"Expected warning for unexpected media_type, got warnings: {warnings}"
        )
        # but overall should be OK because primary video exists
        assert result.ok is True, f"Expected OK, got errors: {result.errors()}"

    def test_mime_type_warning(self, service: VideoPostValidationService) -> None:
        post = _make_post()
        assets = [_make_asset(mime_type="video/avi")]
        targets = [_make_target(platform="instagram")]
        result = service.validate_post(post, assets, targets)
        warnings = result.warnings()
        warning_codes = {w.code for w in warnings}
        assert "ASSET_MIME_TYPE_NOT_ALLOWED" in warning_codes

    def test_caption_too_long_for_platform(self, service: VideoPostValidationService) -> None:
        long_text = "x" * 5001  # YouTube max is 5000
        post = _make_post(text=long_text)
        assets = [_make_asset()]
        targets = [_make_target(platform="youtube")]
        result = service.validate_post(post, assets, targets)
        assert result.ok is False
        codes = {i.code for i in result.issues}
        assert "POST_CAPTION_TOO_LONG" in codes

    def test_title_too_long_for_youtube(self, service: VideoPostValidationService) -> None:
        long_title = "x" * 101  # YouTube max is 100
        post = _make_post(title=long_title)
        assets = [_make_asset()]
        targets = [_make_target(platform="youtube")]
        result = service.validate_post(post, assets, targets)
        assert result.ok is False
        codes = {i.code for i in result.issues}
        assert "POST_TITLE_TOO_LONG" in codes

    def test_duration_too_long_for_tiktok(self, service: VideoPostValidationService) -> None:
        post = _make_post()
        assets = [_make_asset(duration_seconds=200.0)]
        targets = [_make_target(platform="tiktok")]
        result = service.validate_post(post, assets, targets)
        assert result.ok is False
        codes = {i.code for i in result.issues}
        assert "ASSET_DURATION_TOO_LONG" in codes

    def test_duration_too_long_for_instagram(self, service: VideoPostValidationService) -> None:
        post = _make_post()
        assets = [_make_asset(duration_seconds=120.0)]
        targets = [_make_target(platform="instagram")]
        result = service.validate_post(post, assets, targets)
        assert result.ok is False
        codes = {i.code for i in result.issues}
        assert "ASSET_DURATION_TOO_LONG" in codes

    def test_file_size_too_large_for_tiktok(self, service: VideoPostValidationService) -> None:
        post = _make_post()
        assets = [_make_asset(file_size=600_000_000)]  # 600 MB > 500 MB
        targets = [_make_target(platform="tiktok")]
        result = service.validate_post(post, assets, targets)
        assert result.ok is False
        codes = {i.code for i in result.issues}
        assert "ASSET_FILE_SIZE_TOO_LARGE" in codes

    def test_instagram_requires_cloudinary_url(self, service: VideoPostValidationService) -> None:
        post = _make_post()
        assets = [_make_asset(cloudinary_url=None)]
        targets = [_make_target(platform="instagram")]
        result = service.validate_post(post, assets, targets)
        codes = {i.code for i in result.issues}
        assert "ASSET_NO_CLOUDINARY_URL" in codes

    def test_dimensions_unknown_do_not_fail(self, service: VideoPostValidationService) -> None:
        post = _make_post()
        assets = [_make_asset(width=None, height=None)]
        targets = [_make_target(platform="tiktok")]
        result = service.validate_post(post, assets, targets)
        assert result.ok is True, f"Expected OK with unknown dimensions, got: {result.issues}"

    def test_extension_warning(self, service: VideoPostValidationService) -> None:
        post = _make_post()
        assets = [_make_asset(original_filename="video.avi")]
        targets = [_make_target(platform="instagram")]
        result = service.validate_post(post, assets, targets)
        warnings = result.warnings()
        warning_codes = {w.code for w in warnings}
        assert "ASSET_EXTENSION_NOT_ALLOWED" in warning_codes

    def test_cloudinary_url_not_required_for_tiktok(self, service: VideoPostValidationService) -> None:
        post = _make_post()
        assets = [_make_asset(cloudinary_url=None)]
        targets = [_make_target(platform="tiktok")]
        result = service.validate_post(post, assets, targets)
        assert result.ok is True, f"Expected OK, got issues: {result.issues}"


class TestCustomProfiles:
    def test_custom_profile_overrides_default(self) -> None:
        custom_profile = PlatformVideoProfile(
            platform="tiktok",
            required_media_type="video",
            max_duration_seconds=30.0,
        )
        svc = VideoPostValidationService(profiles={"tiktok": custom_profile})
        post = _make_post()
        assets = [_make_asset(duration_seconds=60.0)]
        target = _make_target()
        result = svc.validate_post(post, assets, [target])
        assert result.ok is False
        codes = {i.code for i in result.issues}
        assert "ASSET_DURATION_TOO_LONG" in codes

    def test_custom_profile_additional_platform(self) -> None:
        custom_profile = PlatformVideoProfile(
            platform="vk",
            required_media_type="video",
            caption_max_length=1000,
        )
        svc = VideoPostValidationService(profiles={"vk": custom_profile})
        post = _make_post()
        assets = [_make_asset()]
        target = _make_target(platform="vk")
        result = svc.validate_post(post, assets, [target])
        assert result.ok is True, f"Expected OK for custom platform, got: {result.issues}"


class TestTargetMismatch:
    def test_target_post_id_mismatch(self, service: VideoPostValidationService) -> None:
        post = _make_post(post_id=1)
        assets = [_make_asset()]
        target = _make_target(post_id=999)
        result = service.validate_post(post, assets, [target])
        codes = {i.code for i in result.issues}
        assert "TARGET_POST_ID_MISMATCH" in codes


class TestServiceIsolation:
    def test_service_does_not_touch_db(self, service: VideoPostValidationService) -> None:
        """Validation should only use in-memory data, never call DB."""
        import inspect
        source = inspect.getsource(type(service))
        # No SQL imports or DB references
        assert "import sqlite3" not in source
        assert "database" not in source.lower()
        assert "db." not in source
        assert "init_schema" not in source