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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def validation() -> VideoPostValidationService:
    return VideoPostValidationService()


@pytest.fixture
def planner(validation: VideoPostValidationService) -> VideoPublicationPlanner:
    return VideoPublicationPlanner(validation=validation)


def _make_video_asset(
    source: str = "https://example.com/video.mp4",
    media_type: str = VideoMediaType.VIDEO.value,
) -> VideoAsset:
    return VideoAsset(
        post_id=0,
        source=source,
        media_type=media_type,
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
    destination: str | None = None,
) -> VideoTarget:
    return VideoTarget(
        id=target_id,
        post_id=post_id,
        platform=platform,
        account_id=account_id,
        status=status,
        destination=destination,
    )


def _make_post(
    post_id: int = 1,
    status: str = VideoPostStatus.READY.value,
    scheduled_at: str | None = None,
    title: str | None = "Test Post",
    text: str = "Hello",
) -> VideoPost:
    return VideoPost(
        id=post_id,
        external_post_id="ext-test",
        title=title,
        text=text,
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


class TestPublicationDecisionType:
    def test_all_values_present(self) -> None:
        values = {v.value for v in PublicationDecisionType}
        expected = {"ready", "scheduled", "waiting", "retry", "blocked", "already_published"}
        assert values == expected


class TestPublicationPlanHelpers:
    def test_ready_targets(self) -> None:
        plan = PublicationPlan(post_id=1, decisions=[
            PublicationDecision(target_id=1, platform="tiktok",
                                decision=PublicationDecisionType.READY, reason="ok"),
            PublicationDecision(target_id=2, platform="instagram",
                                decision=PublicationDecisionType.BLOCKED, reason="blocked"),
        ])
        assert len(plan.ready_targets()) == 1
        assert plan.ready_targets()[0].target_id == 1

    def test_blocked_targets(self) -> None:
        plan = PublicationPlan(post_id=1, decisions=[
            PublicationDecision(target_id=1, platform="tiktok",
                                decision=PublicationDecisionType.READY, reason="ok"),
            PublicationDecision(target_id=2, platform="instagram",
                                decision=PublicationDecisionType.BLOCKED, reason="blocked"),
        ])
        assert len(plan.blocked_targets()) == 1
        assert plan.blocked_targets()[0].target_id == 2

    def test_retry_targets(self) -> None:
        plan = PublicationPlan(post_id=1, decisions=[
            PublicationDecision(target_id=1, platform="tiktok",
                                decision=PublicationDecisionType.RETRY, reason="retry"),
            PublicationDecision(target_id=2, platform="instagram",
                                decision=PublicationDecisionType.READY, reason="ok"),
        ])
        assert len(plan.retry_targets()) == 1
        assert plan.retry_targets()[0].target_id == 1


class TestReadyDecision:
    def test_ready_when_post_ready_target_pending(self, planner: VideoPublicationPlanner) -> None:
        post = _make_post(status=VideoPostStatus.READY.value)
        target = _make_target(status=VideoTargetStatus.PENDING.value)
        bundle = _make_bundle(post, [target])
        plan = planner.build_plan(bundle)
        assert len(plan.decisions) == 1
        assert plan.decisions[0].decision == PublicationDecisionType.READY

    def test_ready_build_plan_returns_plan(self, planner: VideoPublicationPlanner) -> None:
        post = _make_post(status=VideoPostStatus.READY.value)
        target = _make_target(status=VideoTargetStatus.PENDING.value)
        bundle = _make_bundle(post, [target])
        plan = planner.build_plan(bundle)
        assert isinstance(plan, PublicationPlan)
        assert plan.post_id == post.id


class TestScheduledDecision:
    def test_scheduled_when_future_date(self, planner: VideoPublicationPlanner) -> None:
        future = (datetime.utcnow() + timedelta(hours=6)).isoformat()
        post = _make_post(status=VideoPostStatus.READY.value, scheduled_at=future)
        target = _make_target(status=VideoTargetStatus.PENDING.value)
        bundle = _make_bundle(post, [target])
        plan = planner.build_plan(bundle)
        assert plan.decisions[0].decision == PublicationDecisionType.SCHEDULED
        assert plan.decisions[0].scheduled_at == future

    def test_not_scheduled_when_past_date(self, planner: VideoPublicationPlanner) -> None:
        past = (datetime.utcnow() - timedelta(hours=6)).isoformat()
        post = _make_post(status=VideoPostStatus.READY.value, scheduled_at=past)
        target = _make_target(status=VideoTargetStatus.PENDING.value)
        bundle = _make_bundle(post, [target])
        plan = planner.build_plan(bundle)
        # Past scheduled_at falls through to READY
        assert plan.decisions[0].decision == PublicationDecisionType.READY


class TestWaitingDecision:
    def test_waiting_when_post_is_draft(self, planner: VideoPublicationPlanner) -> None:
        post = _make_post(status=VideoPostStatus.DRAFT.value)
        target = _make_target(status=VideoTargetStatus.PENDING.value)
        bundle = _make_bundle(post, [target])
        plan = planner.build_plan(bundle)
        assert plan.decisions[0].decision == PublicationDecisionType.WAITING


class TestAlreadyPublishedDecision:
    def test_already_published(self, planner: VideoPublicationPlanner) -> None:
        post = _make_post(status=VideoPostStatus.READY.value)
        target = _make_target(status=VideoTargetStatus.PUBLISHED.value)
        bundle = _make_bundle(post, [target])
        plan = planner.build_plan(bundle)
        assert plan.decisions[0].decision == PublicationDecisionType.ALREADY_PUBLISHED


class TestBlockedDecision:
    def test_blocked_when_validation_fails(self, planner: VideoPublicationPlanner) -> None:
        """Create a post with no assets and an unsupported platform."""
        post = _make_post(status=VideoPostStatus.READY.value)
        target = _make_target(platform="unsupported", status=VideoTargetStatus.PENDING.value)
        # Bundle with no video asset to trigger POST_NO_VIDEO_ASSET
        bundle = _make_bundle(post, [target], assets=[])
        plan = planner.build_plan(bundle)
        assert plan.decisions[0].decision == PublicationDecisionType.BLOCKED

    def test_blocked_draft_skips_validation(self, planner: VideoPublicationPlanner) -> None:
        """Draft posts should be WAITING, not BLOCKED, even if validation would fail."""
        post = _make_post(status=VideoPostStatus.DRAFT.value)
        target = _make_target(platform="unsupported", status=VideoTargetStatus.PENDING.value)
        bundle = _make_bundle(post, [target], assets=[])
        plan = planner.build_plan(bundle)
        # Draft takes priority over BLOCKED
        assert plan.decisions[0].decision == PublicationDecisionType.WAITING


class TestRetryDecision:
    def test_retry_when_target_failed(self, planner: VideoPublicationPlanner) -> None:
        post = _make_post(status=VideoPostStatus.READY.value)
        target = _make_target(status=VideoTargetStatus.FAILED.value)
        bundle = _make_bundle(post, [target])
        plan = planner.build_plan(bundle)
        assert plan.decisions[0].decision == PublicationDecisionType.RETRY

    def test_retry_not_for_pending_target(self, planner: VideoPublicationPlanner) -> None:
        """A pending target on a ready post should be READY, not RETRY."""
        post = _make_post(status=VideoPostStatus.READY.value)
        target = _make_target(status=VideoTargetStatus.PENDING.value)
        bundle = _make_bundle(post, [target])
        plan = planner.build_plan(bundle)
        assert plan.decisions[0].decision == PublicationDecisionType.READY


class TestPlannerIsolation:
    """Verify the planner does not import / touch forbidden modules."""

    def test_planner_does_not_import_scheduler(self) -> None:
        import autoposter_bot.video_planner as mod
        source = inspect.getsource(mod)
        assert "scheduler" not in source

    def test_planner_does_not_import_publishers(self) -> None:
        import autoposter_bot.video_planner as mod
        source = inspect.getsource(mod)
        lines = source.splitlines()
        import_lines = [l for l in lines if l.strip().startswith("import ") or l.strip().startswith("from ")]
        for line in import_lines:
            assert "publisher" not in line.lower(), f"Import line contains 'publisher': {line!r}"

    def test_planner_does_not_import_admin_bot(self) -> None:
        import autoposter_bot.video_planner as mod
        source = inspect.getsource(mod)
        assert "admin_bot" not in source

    def test_planner_does_not_call_db(self) -> None:
        import autoposter_bot.video_planner as mod
        source = inspect.getsource(mod)
        assert "sqlite3" not in source
        assert ".execute(" not in source


class TestBuildTargetDecision:
    def test_individual_decision(self, planner: VideoPublicationPlanner) -> None:
        post = _make_post(status=VideoPostStatus.READY.value)
        target = _make_target(status=VideoTargetStatus.PENDING.value)
        decision = planner.build_target_decision(post, target)
        assert isinstance(decision, PublicationDecision)
        assert decision.decision == PublicationDecisionType.READY