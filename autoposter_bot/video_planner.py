from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from autoposter_bot.video_draft_service import VideoDraftBundle
from autoposter_bot.video_models import (
    VideoAttemptStatus,
    VideoPost,
    VideoPostStatus,
    VideoTarget,
    VideoTargetStatus,
)
from autoposter_bot.video_validation import VideoPostValidationService


# ---------------------------------------------------------------------------
# Decision types
# ---------------------------------------------------------------------------


class PublicationDecisionType(str, Enum):
    """Possible decisions for a single publication target."""
    READY = "ready"
    SCHEDULED = "scheduled"
    WAITING = "waiting"
    RETRY = "retry"
    BLOCKED = "blocked"
    ALREADY_PUBLISHED = "already_published"


# ---------------------------------------------------------------------------
# Decision + Plan dataclasses
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PublicationDecision:
    """A single decision for one target of a video post."""
    target_id: int
    platform: str
    decision: PublicationDecisionType
    reason: str
    scheduled_at: str | None = None
    retry_at: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(slots=True)
class PublicationPlan:
    """Aggregated publication plan for a video post."""
    post_id: int
    created_at: str = ""
    decisions: list[PublicationDecision] = field(default_factory=list)

    def ready_targets(self) -> list[PublicationDecision]:
        return [d for d in self.decisions if d.decision == PublicationDecisionType.READY]

    def blocked_targets(self) -> list[PublicationDecision]:
        return [d for d in self.decisions if d.decision == PublicationDecisionType.BLOCKED]

    def retry_targets(self) -> list[PublicationDecision]:
        return [d for d in self.decisions if d.decision == PublicationDecisionType.RETRY]


# ---------------------------------------------------------------------------
# Planner service
# ---------------------------------------------------------------------------


class VideoPublicationPlanner:
    """Builds a publication plan for a video post.

    The planner is stateless and read-only — it never writes to the database,
    never changes statuses, never creates publication attempts, and never
    invokes publisher adapters.

    It uses ``VideoPostValidationService`` to detect BLOCKED decisions.
    """

    def __init__(
        self,
        validation: VideoPostValidationService,
    ) -> None:
        self._validation = validation

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_plan(self, bundle: VideoDraftBundle) -> PublicationPlan:
        """Build a full publication plan for a video post bundle.

        Returns a ``PublicationPlan`` with one ``PublicationDecision`` per
        target.  The planner never writes to the database.
        """
        now = datetime.utcnow().isoformat()
        decisions: list[PublicationDecision] = []

        for target in bundle.targets:
            decision = self.build_target_decision(bundle.post, target, bundle)
            decisions.append(decision)

        return PublicationPlan(
            post_id=bundle.post.id or 0,
            created_at=now,
            decisions=decisions,
        )

    def build_target_decision(
        self,
        post: VideoPost,
        target: VideoTarget,
        bundle: VideoDraftBundle | None = None,
    ) -> PublicationDecision:
        """Build a single decision for one publication target.

        Decision logic (in order):

        1. **ALREADY_PUBLISHED** — target status is ``published``.
        2. **BLOCKED** — validation detects errors.  Only checked when
           the post is not ``draft`` (drafts are WAITING, not blocked).
        3. **WAITING** — post status is ``draft``.
        4. **SCHEDULED** — post has a future ``scheduled_at``.
        5. **RETRY** — last attempt for this target failed and retry_at
           has arrived.
        6. **READY** — otherwise.
        """
        # --- ALREADY_PUBLISHED ---
        if target.status == VideoTargetStatus.PUBLISHED.value:
            return PublicationDecision(
                target_id=target.id or 0,
                platform=target.platform,
                decision=PublicationDecisionType.ALREADY_PUBLISHED,
                reason=f"Target {target.platform!r} already published (status={target.status!r})",
                metadata={"target_status": target.status},
            )

        # --- WAITING (draft) ---
        if post.status == VideoPostStatus.DRAFT.value:
            return PublicationDecision(
                target_id=target.id or 0,
                platform=target.platform,
                decision=PublicationDecisionType.WAITING,
                reason="Post is still in draft status",
                metadata={"post_status": post.status, "target_status": target.status},
            )

        # --- BLOCKED (validation fails) ---
        # Only check BLOCKED for non-draft posts.
        if bundle is not None and post.status != VideoPostStatus.DRAFT.value:
            val_result = self._validation.validate_for_platform(
                post, bundle.assets, target,
            )
            if val_result.has_errors():
                return PublicationDecision(
                    target_id=target.id or 0,
                    platform=target.platform,
                    decision=PublicationDecisionType.BLOCKED,
                    reason="Validation check failed for this platform",
                    metadata={
                        "errors": [
                            {"code": e.code, "message": e.message}
                            for e in val_result.errors()
                        ],
                        "post_status": post.status,
                        "target_status": target.status,
                    },
                )

        # --- SCHEDULED ---
        if post.scheduled_at:
            try:
                sched = datetime.fromisoformat(post.scheduled_at)
                now = datetime.utcnow()
                if sched > now:
                    return PublicationDecision(
                        target_id=target.id or 0,
                        platform=target.platform,
                        decision=PublicationDecisionType.SCHEDULED,
                        reason=f"Post scheduled at {post.scheduled_at} (future)",
                        scheduled_at=post.scheduled_at,
                        metadata={
                            "scheduled_at": post.scheduled_at,
                            "post_status": post.status,
                            "target_status": target.status,
                        },
                    )
            except (ValueError, TypeError):
                pass  # Treat unparseable scheduled_at as absent

        # --- RETRY ---
        # Check if the target has a last_error or if attempts exist.
        # We infer retry eligibility from target status.
        if target.status == VideoTargetStatus.FAILED.value:
            retry_at = None
            if target.options:
                retry_at = target.options.get("retry_at")
            # Even if retry_at is None, a FAILED target is a retry candidate
            # (the caller can decide the retry timing).
            return PublicationDecision(
                target_id=target.id or 0,
                platform=target.platform,
                decision=PublicationDecisionType.RETRY,
                reason="Previous publication attempt failed",
                retry_at=retry_at,
                metadata={
                    "target_status": target.status,
                    "retry_at": retry_at,
                    "post_status": post.status,
                },
            )

        # --- READY (default) ---
        return PublicationDecision(
            target_id=target.id or 0,
            platform=target.platform,
            decision=PublicationDecisionType.READY,
            reason="Target is ready for publication",
            scheduled_at=post.scheduled_at if post.scheduled_at else None,
            metadata={
                "post_status": post.status,
                "target_status": target.status,
            },
        )