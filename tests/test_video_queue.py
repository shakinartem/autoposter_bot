from __future__ import annotations

import inspect
from datetime import datetime, timedelta

import pytest

from autoposter_bot.video_draft_service import VideoDraftBundle
from autoposter_bot.video_models import (
    VideoAsset,
    VideoMediaType,
    VideoPost,
    VideoPostStatus,
    VideoTarget,
    VideoTargetStatus,
)
from autoposter_bot.video_validation import VideoPostValidationService
from autoposter_bot.video_planner import (
    PublicationDecision,
    PublicationDecisionType,
    PublicationPlan,
    VideoPublicationPlanner,
)
from autoposter_bot.video_queue import (
    QueueBatch,
    QueueItem,
    QueueItemStatus,
    NullPublisherDispatcher,
    PublisherDispatcherProtocol,
    VideoQueueService,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def validation() -> VideoPostValidationService:
    return VideoPostValidationService()


@pytest.fixture
def planner(validation: VideoPostValidationService) -> VideoPublicationPlanner:
    return VideoPublicationPlanner(validation=validation)


@pytest.fixture
def queue_service(planner: VideoPublicationPlanner) -> VideoQueueService:
    return VideoQueueService(planner=planner)


def _make_video_asset() -> VideoAsset:
    return VideoAsset(
        post_id=0,
        source="https://example.com/video.mp4",
        media_type=VideoMediaType.VIDEO.value,
        order_index=0,
        original_filename="video.mp4",
        mime_type="video/mp4",
        duration_seconds=30.0,
        width=1920,
        height=1080,
        file_size=10_000_000,
    )


def _make_target(
    target_id: int = 1,
    post_id: int = 1,
    platform: str = "tiktok",
    account_id: int = 100,
    status: str = VideoTargetStatus.PENDING.value,
) -> VideoTarget:
    return VideoTarget(
        id=target_id,
        post_id=post_id,
        platform=platform,
        account_id=account_id,
        status=status,
    )


def _make_post(
    post_id: int = 1,
    status: str = VideoPostStatus.READY.value,
    scheduled_at: str | None = None,
) -> VideoPost:
    return VideoPost(
        id=post_id,
        external_post_id="ext-test",
        title="Test Post",
        text="Hello",
        status=status,
        scheduled_at=scheduled_at,
    )


def _make_bundle(
    post: VideoPost,
    targets: list[VideoTarget],
    assets: list[VideoAsset] | None = None,
) -> VideoDraftBundle:
    if assets is None:
        assets = [_make_video_asset()]
    return VideoDraftBundle(post=post, assets=assets, targets=targets)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestQueueDecisions:
    """Verify that build_queue respects PublicationDecisionType."""

    def test_ready_targets_are_queued(
        self, queue_service: VideoQueueService, planner: VideoPublicationPlanner,
    ) -> None:
        post = _make_post(status=VideoPostStatus.READY.value)
        target = _make_target(status=VideoTargetStatus.PENDING.value)
        bundle = _make_bundle(post, [target])
        batch = queue_service.build_queue(bundle)
        assert len(batch.items) == 1
        assert batch.items[0].status == QueueItemStatus.QUEUED.value

    def test_retry_targets_are_pending(
        self, queue_service: VideoQueueService, planner: VideoPublicationPlanner,
    ) -> None:
        post = _make_post(status=VideoPostStatus.READY.value)
        target = _make_target(status=VideoTargetStatus.FAILED.value)
        bundle = _make_bundle(post, [target])
        batch = queue_service.build_queue(bundle)
        assert len(batch.items) == 1
        assert batch.items[0].status == QueueItemStatus.PENDING.value

    def test_blocked_targets_skipped(
        self, queue_service: VideoQueueService, planner: VideoPublicationPlanner,
    ) -> None:
        post = _make_post(status=VideoPostStatus.READY.value)
        target = _make_target(platform="unsupported", status=VideoTargetStatus.PENDING.value)
        bundle = _make_bundle(post, [target], assets=[])
        batch = queue_service.build_queue(bundle)
        assert len(batch.items) == 0

    def test_waiting_targets_skipped(
        self, queue_service: VideoQueueService, planner: VideoPublicationPlanner,
    ) -> None:
        post = _make_post(status=VideoPostStatus.DRAFT.value)
        target = _make_target(status=VideoTargetStatus.PENDING.value)
        bundle = _make_bundle(post, [target])
        batch = queue_service.build_queue(bundle)
        assert len(batch.items) == 0

    def test_scheduled_targets_skipped(
        self, queue_service: VideoQueueService, planner: VideoPublicationPlanner,
    ) -> None:
        future = (datetime.utcnow() + timedelta(hours=6)).isoformat()
        post = _make_post(status=VideoPostStatus.READY.value, scheduled_at=future)
        target = _make_target(status=VideoTargetStatus.PENDING.value)
        bundle = _make_bundle(post, [target])
        batch = queue_service.build_queue(bundle)
        assert len(batch.items) == 0

    def test_already_published_skipped(
        self, queue_service: VideoQueueService, planner: VideoPublicationPlanner,
    ) -> None:
        post = _make_post(status=VideoPostStatus.READY.value)
        target = _make_target(status=VideoTargetStatus.PUBLISHED.value)
        bundle = _make_bundle(post, [target])
        batch = queue_service.build_queue(bundle)
        assert len(batch.items) == 0


class TestQueueOperations:
    def test_enqueue_adds_pending_items(
        self, queue_service: VideoQueueService, planner: VideoPublicationPlanner,
    ) -> None:
        post = _make_post(status=VideoPostStatus.READY.value)
        target = _make_target(status=VideoTargetStatus.PENDING.value)
        bundle = _make_bundle(post, [target])
        queue_service.enqueue(bundle)
        ready = queue_service.get_ready_items(bundle)
        assert len(ready) == 0  # enqueue adds PENDING, not QUEUED

    def test_dequeue_returns_queued_item(
        self, queue_service: VideoQueueService, planner: VideoPublicationPlanner,
    ) -> None:
        post = _make_post(status=VideoPostStatus.READY.value)
        target = _make_target(status=VideoTargetStatus.PENDING.value)
        bundle = _make_bundle(post, [target])
        queue_service.build_queue(bundle)
        item = queue_service.dequeue()
        assert item is not None
        assert item.status == QueueItemStatus.PROCESSING.value
        assert item.target_id == target.id

    def test_dequeue_returns_none_when_empty(
        self, queue_service: VideoQueueService,
    ) -> None:
        item = queue_service.dequeue()
        assert item is None

    def test_mark_completed(
        self, queue_service: VideoQueueService, planner: VideoPublicationPlanner,
    ) -> None:
        post = _make_post(status=VideoPostStatus.READY.value)
        target = _make_target(status=VideoTargetStatus.PENDING.value)
        bundle = _make_bundle(post, [target])
        queue_service.build_queue(bundle)
        item = queue_service.dequeue()
        assert item is not None
        queue_service.mark_completed(item)
        assert item.status == QueueItemStatus.COMPLETED.value

    def test_mark_failed(
        self, queue_service: VideoQueueService, planner: VideoPublicationPlanner,
    ) -> None:
        post = _make_post(status=VideoPostStatus.READY.value)
        target = _make_target(status=VideoTargetStatus.PENDING.value)
        bundle = _make_bundle(post, [target])
        queue_service.build_queue(bundle)
        item = queue_service.dequeue()
        assert item is not None
        queue_service.mark_failed(item)
        assert item.status == QueueItemStatus.FAILED.value

    def test_multiple_targets(
        self, queue_service: VideoQueueService, planner: VideoPublicationPlanner,
    ) -> None:
        post = _make_post(status=VideoPostStatus.READY.value)
        targets = [
            _make_target(target_id=1, status=VideoTargetStatus.PENDING.value),
            _make_target(target_id=2, status=VideoTargetStatus.PENDING.value),
            _make_target(target_id=3, status=VideoTargetStatus.FAILED.value),
        ]
        bundle = _make_bundle(post, targets)
        batch = queue_service.build_queue(bundle)
        # 2 READY (pending) + 1 RETRY (failed) = 3 items
        assert len(batch.items) == 3
        queued = batch.ready_items()
        pending = batch.pending_items()
        assert len(queued) == 2
        assert len(pending) == 1


class TestQueueBatchHelpers:
    def test_pending_items(self) -> None:
        batch = QueueBatch(items=[
            QueueItem(post_id=1, target_id=1, platform="tiktok", status=QueueItemStatus.QUEUED.value),
            QueueItem(post_id=1, target_id=2, platform="instagram", status=QueueItemStatus.PENDING.value),
            QueueItem(post_id=1, target_id=3, platform="tiktok", status=QueueItemStatus.PENDING.value),
        ])
        assert len(batch.pending_items()) == 2

    def test_ready_items(self) -> None:
        batch = QueueBatch(items=[
            QueueItem(post_id=1, target_id=1, platform="tiktok", status=QueueItemStatus.QUEUED.value),
            QueueItem(post_id=1, target_id=2, platform="instagram", status=QueueItemStatus.PENDING.value),
        ])
        assert len(batch.ready_items()) == 1

    def test_failed_items(self) -> None:
        batch = QueueBatch(items=[
            QueueItem(post_id=1, target_id=1, platform="tiktok", status=QueueItemStatus.FAILED.value),
            QueueItem(post_id=1, target_id=2, platform="instagram", status=QueueItemStatus.QUEUED.value),
        ])
        assert len(batch.failed_items()) == 1

    def test_scheduled_items(self) -> None:
        batch = QueueBatch(items=[
            QueueItem(post_id=1, target_id=1, platform="tiktok", scheduled_at="2025-01-01T00:00:00"),
            QueueItem(post_id=1, target_id=2, platform="instagram", scheduled_at=None),
        ])
        assert len(batch.scheduled_items()) == 1


class TestDispatcherProtocol:
    def test_null_dispatcher_returns_mock(self) -> None:
        dispatcher = NullPublisherDispatcher()
        item = QueueItem(post_id=1, target_id=1, platform="tiktok")
        result = dispatcher.dispatch(item)
        assert result["published"] is False
        assert result["metadata"]["mock"] is True

    def test_dispatcher_protocol_is_callable(self) -> None:
        # Protocol should define dispatch method
        assert hasattr(PublisherDispatcherProtocol, "dispatch")


class TestQueueIsolation:
    """Verify the queue service does not import / touch forbidden modules."""

    def test_queue_does_not_import_scheduler(self) -> None:
        import autoposter_bot.video_queue as mod
        source = inspect.getsource(mod)
        assert "scheduler" not in source

    def test_queue_does_not_import_publishers(self) -> None:
        import autoposter_bot.video_queue as mod
        source = inspect.getsource(mod)
        lines = source.splitlines()
        import_lines = [l for l in lines if l.strip().startswith("import ") or l.strip().startswith("from ")]
        for line in import_lines:
            assert "publisher" not in line.lower(), f"Import line contains 'publisher': {line!r}"

    def test_queue_does_not_import_admin_bot(self) -> None:
        import autoposter_bot.video_queue as mod
        source = inspect.getsource(mod)
        assert "admin_bot" not in source

    def test_queue_does_not_import_service(self) -> None:
        import autoposter_bot.video_queue as mod
        source = inspect.getsource(mod)
        assert "from autoposter_bot.service" not in source
        assert "import service" not in source.lower()