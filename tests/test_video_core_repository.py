from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from autoposter_bot.db import Database


@pytest.fixture
def db() -> Database:
    """Create a temporary Database with initialized schema for each test."""
    tmpdir = TemporaryDirectory()
    db_path = Path(tmpdir.name) / "test_video.db"
    database = Database(db_path)
    database.init_schema()
    yield database
    tmpdir.cleanup()


def _now() -> str:
    return datetime.utcnow().isoformat()


class TestVideoPostCRUD:
    def test_create_and_get_video_post(self, db: Database) -> None:
        post_id = db.create_video_post(
            external_post_id="ext-001",
            text="Hello video world",
            title="My First Video",
            owner_user_id=1,
            status="draft",
        )
        assert post_id > 0

        row = db.get_video_post(post_id)
        assert row is not None
        assert row["external_post_id"] == "ext-001"
        assert row["text"] == "Hello video world"
        assert row["title"] == "My First Video"
        assert row["status"] == "draft"
        assert row["owner_user_id"] == 1

    def test_update_video_post_status(self, db: Database) -> None:
        post_id = db.create_video_post(
            external_post_id="ext-002", text="Test status update"
        )
        updated = db.update_video_post_status(post_id, "published")
        assert updated is True

        row = db.get_video_post(post_id)
        assert row is not None
        assert row["status"] == "published"

    def test_update_video_post_status_nonexistent(self, db: Database) -> None:
        updated = db.update_video_post_status(99999, "published")
        assert updated is False

    def test_list_video_posts_by_status(self, db: Database) -> None:
        db.create_video_post(external_post_id="ext-003", text="Post A", status="draft")
        db.create_video_post(external_post_id="ext-004", text="Post B", status="draft")
        db.create_video_post(external_post_id="ext-005", text="Post C", status="published")

        drafts = db.list_video_posts_by_status("draft")
        assert len(drafts) == 2

        published = db.list_video_posts_by_status("published")
        assert len(published) == 1

    def test_list_video_posts_by_status_limit(self, db: Database) -> None:
        for i in range(5):
            db.create_video_post(
                external_post_id=f"ext-limit-{i}", text=f"Post {i}", status="draft"
            )

        result = db.list_video_posts_by_status("draft", limit=2)
        assert len(result) == 2


class TestVideoAssetCRUD:
    def test_create_and_list_video_assets(self, db: Database) -> None:
        post_id = db.create_video_post(
            external_post_id="asset-test-post", text="Post with assets"
        )

        asset1_id = db.create_video_asset(
            post_id=post_id,
            source="https://example.com/video.mp4",
            media_type="video",
            order_index=0,
            original_filename="video.mp4",
            file_size=1024000,
            mime_type="video/mp4",
            duration_seconds=30.5,
            width=1920,
            height=1080,
            aspect_ratio="16:9",
            processed=0,
            options={"quality": "hd"},
        )
        assert asset1_id > 0

        asset2_id = db.create_video_asset(
            post_id=post_id,
            source="https://example.com/thumb.jpg",
            media_type="thumbnail",
            order_index=1,
        )
        assert asset2_id > 0

        assets = db.list_video_assets(post_id)
        assert len(assets) == 2
        assert assets[0]["media_type"] == "video"
        assert assets[1]["media_type"] == "thumbnail"

        # Verify JSON field roundtrip
        opts = json.loads(assets[0]["options_json"] or "{}")
        assert opts.get("quality") == "hd"

    def test_list_video_assets_empty(self, db: Database) -> None:
        assets = db.list_video_assets(99999)
        assert assets == []


class TestVideoTargetCRUD:
    def test_create_and_list_video_targets(self, db: Database) -> None:
        post_id = db.create_video_post(
            external_post_id="target-test-post", text="Post with targets"
        )

        target1_id = db.create_video_target(
            post_id=post_id,
            account_id=10,
            platform="instagram",
            destination="my_account",
            options={"schedule": "now"},
        )
        assert target1_id > 0

        target2_id = db.create_video_target(
            post_id=post_id,
            account_id=20,
            platform="tiktok",
        )
        assert target2_id > 0

        targets = db.list_video_targets(post_id)
        assert len(targets) == 2
        assert targets[0]["platform"] == "instagram"
        assert targets[1]["platform"] == "tiktok"

    def test_update_video_target_status(self, db: Database) -> None:
        post_id = db.create_video_post(
            external_post_id="target-status-test", text="Test target status"
        )
        target_id = db.create_video_target(
            post_id=post_id, account_id=1, platform="youtube"
        )

        updated = db.update_video_target_status(target_id, "published")
        assert updated is True

        targets = db.list_video_targets(post_id)
        assert targets[0]["status"] == "published"

    def test_update_video_target_status_with_error(self, db: Database) -> None:
        post_id = db.create_video_post(
            external_post_id="target-error-test", text="Test error"
        )
        target_id = db.create_video_target(
            post_id=post_id, account_id=1, platform="youtube"
        )

        updated = db.update_video_target_status(
            target_id, "failed", error_message="Permission denied"
        )
        assert updated is True

        targets = db.list_video_targets(post_id)
        opts = json.loads(targets[0]["options_json"] or "{}")
        assert opts.get("last_error") == "Permission denied"


class TestVideoPublicationAttemptCRUD:
    def test_create_and_list_attempts(self, db: Database) -> None:
        post_id = db.create_video_post(
            external_post_id="attempt-test-post", text="Post with attempts"
        )
        target_id = db.create_video_target(
            post_id=post_id, account_id=1, platform="instagram"
        )

        attempt1_id = db.create_video_publication_attempt(
            post_id=post_id,
            target_id=target_id,
            platform="instagram",
            attempt_number=1,
            status="started",
        )
        assert attempt1_id > 0

        attempt2_id = db.create_video_publication_attempt(
            post_id=post_id,
            target_id=target_id,
            platform="instagram",
            attempt_number=2,
            status="succeeded",
            external_id="ig-media-123",
            started_at=_now(),
            finished_at=_now(),
        )
        assert attempt2_id > 0

        attempts = db.list_video_publication_attempts(target_id)
        assert len(attempts) == 2
        assert attempts[0]["status"] == "started"
        assert attempts[1]["status"] == "succeeded"
        assert attempts[1]["external_id"] == "ig-media-123"

    def test_list_attempts_empty(self, db: Database) -> None:
        attempts = db.list_video_publication_attempts(99999)
        assert attempts == []


class TestReinitDoesNotBreak:
    def test_reinit_schema_preserves_data(self, db: Database) -> None:
        """Re-running init_schema should not delete or corrupt existing data."""
        post_id = db.create_video_post(
            external_post_id="reinit-test", text="Before reinit"
        )
        db.init_schema()

        row = db.get_video_post(post_id)
        assert row is not None
        assert row["text"] == "Before reinit"

        # Can still create new records
        post_id2 = db.create_video_post(
            external_post_id="reinit-test-2", text="After reinit"
        )
        assert post_id2 > 0


class TestOldTablesStillWork:
    def test_create_job_still_works(self, db: Database) -> None:
        from autoposter_bot.models import MediaItem

        job_id = db.create_job(
            post_id="old-job-001",
            content_type="text",
            text="Old job still works",
            scheduled_at=None,
            media_items=[
                MediaItem(source="https://example.com/img.jpg", media_type="image")
            ],
            account_ids=[1],
            metadata={"source": "test"},
            owner_user_id=1,
        )
        assert job_id > 0

    def test_get_due_jobs_still_works(self, db: Database) -> None:
        from autoposter_bot.models import MediaItem

        db.create_job(
            post_id="due-job-001",
            content_type="text",
            text="Due job",
            scheduled_at=None,
            media_items=[],
            account_ids=[1],
            metadata={},
            owner_user_id=1,
        )
        jobs = db.get_due_jobs(datetime.utcnow())
        assert any(j.post_id == "due-job-001" for j in jobs)