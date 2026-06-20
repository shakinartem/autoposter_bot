from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable

from autoposter_bot.video_draft_service import VideoDraftBundle
from autoposter_bot.video_models import VideoTargetStatus
from autoposter_bot.video_planner import (
    PublicationDecision,
    PublicationDecisionType,
    PublicationPlan,
    VideoPublicationPlanner,
)


# ---------------------------------------------------------------------------
# Queue item statuses
# ---------------------------------------------------------------------------


class QueueItemStatus(str, Enum):
    """Status values for in-memory queue items."""
    PENDING = "pending"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# Queue dataclasses
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class QueueItem:
    """A single item in the video publication queue."""
    post_id: int
    target_id: int
    platform: str
    status: str = QueueItemStatus.PENDING.value
    scheduled_at: str | None = None
    created_at: str = ""
    metadata: dict[str, Any] | None = None

    @property
    def is_active(self) -> bool:
        return self.status in (
            QueueItemStatus.PENDING.value,
            QueueItemStatus.QUEUED.value,
            QueueItemStatus.PROCESSING.value,
        )


@dataclass(slots=True)
class QueueBatch:
    """A batch of queue items for a single video post."""
    created_at: str = ""
    items: list[QueueItem] = field(default_factory=list)

    def pending_items(self) -> list[QueueItem]:
        return [i for i in self.items if i.status == QueueItemStatus.PENDING.value]

    def ready_items(self) -> list[QueueItem]:
        return [i for i in self.items if i.status == QueueItemStatus.QUEUED.value]

    def failed_items(self) -> list[QueueItem]:
        return [i for i in self.items if i.status == QueueItemStatus.FAILED.value]

    def scheduled_items(self) -> list[QueueItem]:
        return [i for i in self.items if i.scheduled_at is not None]


# ---------------------------------------------------------------------------
# Publisher dispatcher protocol + null implementation
# ---------------------------------------------------------------------------


class PublisherDispatcherProtocol:
    """Minimal interface expected from a publisher dispatcher.

    The actual type is checked at runtime by duck typing.
    """
    def dispatch(self, queue_item: QueueItem) -> dict[str, Any]: ...


class NullPublisherDispatcher:
    """A no-op dispatcher that returns a mock result without publishing."""

    def dispatch(self, queue_item: QueueItem) -> dict[str, Any]:
        return {
            "published": False,
            "external_id": None,
            "error": None,
            "metadata": {"mock": True},
        }


# ---------------------------------------------------------------------------
# Queue service
# ---------------------------------------------------------------------------


class VideoQueueService:
    """In-memory video publication queue.

    The queue service is stateless regarding persistence — all state lives in
    memory during the process lifetime.

    It uses ``VideoPublicationPlanner`` to decide which targets are ready,
    blocked, scheduled, or waiting.
    """

    def __init__(
        self,
        planner: VideoPublicationPlanner,
        dispatcher: PublisherDispatcherProtocol | None = None,
    ) -> None:
        self._planner = planner
        self._dispatcher = dispatcher or NullPublisherDispatcher()
        self._batches: dict[int, QueueBatch] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_queue(self, bundle: VideoDraftBundle) -> QueueBatch:
        """Build a queue batch from a video draft bundle.

        Uses the planner to decide the fate of each target:

        * READY → QUEUED
        * RETRY → PENDING
        * all other decisions → skipped (not added)
        """
        plan = self._planner.build_plan(bundle)
        batch = QueueBatch(created_at=datetime.utcnow().isoformat())
        for decision in plan.decisions:
            if decision.decision == PublicationDecisionType.READY:
                batch.items.append(QueueItem(
                    post_id=bundle.post.id or 0,
                    target_id=decision.target_id,
                    platform=decision.platform,
                    status=QueueItemStatus.QUEUED.value,
                    scheduled_at=None,
                    metadata=decision.metadata,
                ))
            elif decision.decision == PublicationDecisionType.RETRY:
                batch.items.append(QueueItem(
                    post_id=bundle.post.id or 0,
                    target_id=decision.target_id,
                    platform=decision.platform,
                    status=QueueItemStatus.PENDING.value,
                    scheduled_at=decision.retry_at,
                    metadata=decision.metadata,
                ))
            # BLOCKED, WAITING, SCHEDULED, ALREADY_PUBLISHED are skipped
        self._batches[bundle.post.id or 0] = batch
        return batch

    def get_ready_items(self, bundle: VideoDraftBundle) -> list[QueueItem]:
        """Return only QUEUED items for the given bundle."""
        batch = self._batches.get(bundle.post.id or 0)
        if batch is None:
            return []
        return [i for i in batch.items if i.status == QueueItemStatus.QUEUED.value]

    def enqueue(self, bundle: VideoDraftBundle) -> None:
        """Enqueue all READY targets for the given bundle."""
        batch = self._batches.get(bundle.post.id or 0)
        if batch is None:
            batch = QueueBatch(created_at=datetime.utcnow().isoformat())
            self._batches[bundle.post.id or 0] = batch
        for decision in self._planner.build_plan(bundle).decisions:
            if decision.decision == PublicationDecisionType.READY:
                batch.items.append(QueueItem(
                    post_id=bundle.post.id or 0,
                    target_id=decision.target_id,
                    platform=decision.platform,
                    status=QueueItemStatus.PENDING.value,
                    metadata=decision.metadata,
                ))

    def dequeue(self) -> QueueItem | None:
        """Pop the next QUEUED item from any batch and mark it PROCESSING."""
        for batch in self._batches.values():
            for item in batch.items:
                if item.status == QueueItemStatus.QUEUED.value:
                    item.status = QueueItemStatus.PROCESSING.value
                    return item
        return None

    def mark_processing(self, item: QueueItem) -> None:
        """Mark a queue item as PROCESSING."""
        item.status = QueueItemStatus.PROCESSING.value

    def mark_completed(self, item: QueueItem) -> None:
        """Mark a queue item as COMPLETED."""
        item.status = QueueItemStatus.COMPLETED.value

    def mark_failed(self, item: QueueItem) -> None:
        """Mark a queue item as FAILED."""
        item.status = QueueItemStatus.FAILED.value

    # ------------------------------------------------------------------
    # Internal helpers (for tests/inspection)
    # ------------------------------------------------------------------

    def _get_batch(self, post_id: int) -> QueueBatch | None:
        return self._batches.get(post_id)