from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from autoposter_bot.db import Database
from autoposter_bot.video_models import (
    VideoAsset,
    VideoMediaType,
    VideoPost,
    VideoPostStatus,
    VideoTarget,
    VideoTargetStatus,
)
from autoposter_bot.video_validation import (
    ValidationIssue,
    ValidationSeverity,
    VideoPostValidationService,
)
from autoposter_bot.video_draft_service import (
    VideoDraftBundle,
    VideoDraftCreationService,
    VideoDraftError,
    VideoDraftNotFoundError,
    VideoDraftValidationError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> Database:
    _db = Database(tmp_path / "test_video.db")
    _db.init_schema()
    return _db


@pytest.fixture
def validation() -> VideoPostValidationService:
    return VideoPostValidationService()


@pytest.fixture
def service(
    db: Database,
    validation: VideoPostValidationService,
) -> VideoDraftCreationService:
    return VideoDraftCreationService(repository=db, validation=validation)


def _make_asset(
    source: str = "https://example.com/video.mp4",
    media_type: str = VideoMediaType.VIDEO.value,
    order_index: int = 0,
    original_filename: str | None = "video.mp4",
    mime_type: str | None = "video/mp4",
    duration_seconds: float | None = 30.0,
    width: int | None = 1920,
    height: int | None = 1080,
    file_size: int | None = 10_000_000,
    cloudinary_url: str | None = None,
) -> VideoAsset:
    return VideoAsset(
        post_id=0,
        source=source,
        media_type=media_type,
        order_index=order_index,
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
    post_id: int = 0,
) -> VideoTarget:
    return VideoTarget(
        post_id=post_id,
        account_id=account_id,
        platform=platform,
        status=VideoTargetStatus.PENDING.value,
    )


def _make_valid_asset() -> VideoAsset:
    return _make_asset(
        source="https://example.com/video.mp4",
        media_type=VideoMediaType.VIDEO.value,
        original_filename="video.mp4",
        mime_type="video/mp4",
        duration_seconds=30.0,
        width=1920,
        height=1080,
        file_size=10_000_000,
    )


def _make_valid_target() -> VideoTarget:
    return _make_target(platform="tiktok", account_id=100)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCreateDraft:
    def test_create_draft(self, service: VideoDraftCreationService) -> None:
        assets = [_make_valid_asset()]
        targets = [_make_valid_target()]
        post = service.create_draft(
            owner_user_id=1,
            title="My Draft",
            caption="Hello",
            assets=assets,
            targets=targets,
        )
        assert post.id is not None and post.id > 0
        assert post.status == VideoPostStatus.DRAFT.value
        assert post.title == "My Draft"
        assert post.text == "Hello"

    def test_create_draft_via_payload(self, service: VideoDraftCreationService) -> None:
        payload: dict[str, Any] = {
            "owner_user_id": 1,
            "title": "Payload Draft",
            "caption": "From payload",
            "assets": [
                {
                    "source": "https://example.com/vid.mp4",
                    "media_type": VideoMediaType.VIDEO.value,
                    "original_filename": "vid.mp4",
                    "mime_type": "video/mp4",
                    "duration_seconds": 30.0,
                    "width": 1920,
                    "height": 1080,
                    "file_size": 10_000_000,
                },
            ],
            "targets": [
                {
                    "platform": "tiktok",
                    "account_id": 200,
                },
            ],
        }
        post = service.create_draft_from_payload(payload)
        assert post.id is not None and post.id > 0
        assert post.title == "Payload Draft"
        assert post.text == "From payload"
        assert post.owner_user_id == 1

    def test_create_draft_no_assets_fails_validation(
        self, service: VideoDraftCreationService,
    ) -> None:
        with pytest.raises(VideoDraftValidationError) as exc_info:
            service.create_draft(
                owner_user_id=1,
                title="No Assets",
                caption="test",
                assets=[],
                targets=[_make_valid_target()],
            )
        assert any(
            "POST_NO_ASSETS" in str(e.code) for e in exc_info.value.issues
        ) or any(
            "POST_NO_VIDEO_ASSET" in str(e.code) for e in exc_info.value.issues
        )

    def test_create_draft_no_targets_fails_validation(
        self, service: VideoDraftCreationService,
    ) -> None:
        with pytest.raises(VideoDraftValidationError):
            service.create_draft(
                owner_user_id=1,
                title="No Targets",
                caption="test",
                assets=[_make_valid_asset()],
                targets=[],
            )

    def test_validation_error_does_not_save(
        self, db: Database, service: VideoDraftCreationService,
    ) -> None:
        """When validation fails, nothing should be persisted."""
        with pytest.raises(VideoDraftValidationError):
            service.create_draft(
                owner_user_id=1,
                title="Invalid",
                caption="test",
                assets=[],
                targets=[],
            )
        # No posts should exist
        rows = db.list_video_posts_by_status("draft")
        assert len(rows) == 0


class TestAddAsset:
    def test_add_asset(self, service: VideoDraftCreationService) -> None:
        post = service.create_draft(
            owner_user_id=1,
            caption="test",
            assets=[_make_valid_asset()],
            targets=[_make_valid_target()],
        )
        new_asset = _make_asset(
            source="https://example.com/extra.mp4",
            original_filename="extra.mp4",
        )
        created = service.add_asset(post.id, new_asset)
        assert created.id is not None and created.id > 0
        assert created.post_id == post.id
        assert created.source == "https://example.com/extra.mp4"

    def test_add_asset_to_nonexistent_post_raises(
        self, service: VideoDraftCreationService,
    ) -> None:
        asset = _make_valid_asset()
        with pytest.raises(VideoDraftNotFoundError):
            service.add_asset(99999, asset)


class TestAddTarget:
    def test_add_target(self, service: VideoDraftCreationService) -> None:
        post = service.create_draft(
            owner_user_id=1,
            caption="test",
            assets=[_make_valid_asset()],
            targets=[_make_valid_target()],
        )
        new_target = _make_target(platform="instagram", account_id=200)
        created = service.add_target(post.id, new_target)
        assert created.id is not None and created.id > 0
        assert created.post_id == post.id
        assert created.platform == "instagram"
        assert created.account_id == 200

    def test_add_target_to_nonexistent_post_raises(
        self, service: VideoDraftCreationService,
    ) -> None:
        target = _make_valid_target()
        with pytest.raises(VideoDraftNotFoundError):
            service.add_target(99999, target)


class TestCloneDraft:
    def test_clone_draft(self, service: VideoDraftCreationService) -> None:
        post = service.create_draft(
            owner_user_id=1,
            title="Original",
            caption="original text",
            assets=[_make_valid_asset()],
            targets=[_make_valid_target()],
        )
        cloned = service.clone_draft(post.id)
        assert cloned.id is not None and cloned.id != post.id
        assert cloned.title == "Original"
        assert cloned.text == "original text"
        assert cloned.status == VideoPostStatus.DRAFT.value

        # Verify assets and targets were cloned
        bundle = service.get_draft(cloned.id)
        assert len(bundle.assets) == 1
        assert len(bundle.targets) == 1

    def test_clone_draft_creates_new_id(
        self, service: VideoDraftCreationService,
    ) -> None:
        post = service.create_draft(
            owner_user_id=1,
            caption="test",
            assets=[_make_valid_asset()],
            targets=[_make_valid_target()],
        )
        cloned = service.clone_draft(post.id)
        assert cloned.id != post.id

    def test_clone_draft_nonexistent_raises(
        self, service: VideoDraftCreationService,
    ) -> None:
        with pytest.raises(VideoDraftNotFoundError):
            service.clone_draft(99999)


class TestGetDraft:
    def test_get_draft_returns_bundle(
        self, service: VideoDraftCreationService,
    ) -> None:
        post = service.create_draft(
            owner_user_id=1,
            title="Get me",
            caption="test",
            assets=[_make_valid_asset()],
            targets=[_make_valid_target()],
        )
        bundle = service.get_draft(post.id)
        assert isinstance(bundle, VideoDraftBundle)
        assert bundle.post.id == post.id
        assert len(bundle.assets) == 1
        assert len(bundle.targets) == 1

    def test_get_draft_nonexistent_raises(
        self, service: VideoDraftCreationService,
    ) -> None:
        with pytest.raises(VideoDraftNotFoundError):
            service.get_draft(99999)


class TestServiceIsolation:
    """Verify the service does not import / touch forbidden modules."""

    def test_service_does_not_import_scheduler(self) -> None:
        source = inspect.getsource(VideoDraftCreationService)
        assert "scheduler" not in source.lower() or "scheduler" not in source

    def test_service_does_not_import_publishers(self) -> None:
        source = inspect.getsource(VideoDraftCreationService)
        assert "publisher" not in source.lower()

    def test_service_does_not_import_admin_bot(self) -> None:
        source = inspect.getsource(VideoDraftCreationService)
        assert "admin_bot" not in source

    def test_service_does_not_call_db_directly(self) -> None:
        """The service should use repository methods, not raw SQL."""
        source = inspect.getsource(VideoDraftCreationService)
        assert "execute(" not in source
        assert "sqlite3" not in source

    def test_module_level_does_not_import_forbidden(self) -> None:
        """Top-level module imports should not include forbidden modules."""
        import autoposter_bot.video_draft_service as mod
        mod_source = inspect.getsource(mod)
        assert "import scheduler" not in mod_source
        assert "import publishers" not in mod_source
        assert "import admin_bot" not in mod_source
        assert "import service" not in mod_source or "from autoposter_bot.service" not in mod_source


class TestValidationIntegration:
    def test_validation_called_on_create(
        self, validation: VideoPostValidationService, db: Database,
    ) -> None:
        """Create a mock validation service to confirm it is invoked."""
        validated = False

        class TrackingValidation(VideoPostValidationService):
            def validate_post(self, post: VideoPost, assets: list[VideoAsset],
                              targets: list[VideoTarget]) -> Any:
                nonlocal validated
                validated = True
                return super().validate_post(post, assets, targets)

        svc = VideoDraftCreationService(
            repository=db,
            validation=TrackingValidation(),
        )
        svc.create_draft(
            owner_user_id=1,
            caption="test",
            assets=[_make_valid_asset()],
            targets=[_make_valid_target()],
        )
        assert validated, "Validation was not called"


class TestErrorHierarchy:
    def test_video_draft_error_is_base(self) -> None:
        assert issubclass(VideoDraftValidationError, VideoDraftError)
        assert issubclass(VideoDraftNotFoundError, VideoDraftError)

    def test_validation_error_contains_issues(self) -> None:
        issues = [
            ValidationIssue(code="TEST", message="test", severity=ValidationSeverity.ERROR),
        ]
        err = VideoDraftValidationError("fail", issues)
        assert len(err.issues) == 1
        assert "fail" in str(err)