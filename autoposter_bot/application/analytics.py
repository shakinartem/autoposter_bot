from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from autoposter_bot.analytics.base import CollectorRegistry
from autoposter_bot.infrastructure.analytics_store import AnalyticsStore, MILESTONES


MILESTONE_TOLERANCE: dict[str, timedelta] = {
    "1h": timedelta(hours=2),
    "6h": timedelta(hours=3),
    "24h": timedelta(hours=6),
    "72h": timedelta(hours=12),
    "7d": timedelta(hours=24),
}


class AnalyticsCollectionService:
    def __init__(
        self,
        *,
        analytics: AnalyticsStore,
        content_store: Any,
        collectors: CollectorRegistry,
    ) -> None:
        self.analytics = analytics
        self.content_store = content_store
        self.collectors = collectors

    def run_once(self, *, now: datetime | None = None, limit: int = 250) -> dict[str, int]:
        now = self._aware(now or datetime.now(timezone.utc))
        candidates = self.analytics.due_candidates(now=now, limit=limit)
        stats = {
            "candidates": len(candidates),
            "collected": 0,
            "missed": 0,
            "skipped": 0,
            "failed": 0,
        }
        delays = dict(MILESTONES)

        for candidate in candidates:
            collector = self.collectors.get(str(candidate["platform"]))
            if collector is None:
                stats["skipped"] += 1
                continue

            publication_id = str(candidate["publication_id"])
            published_at = self._aware(candidate["published_at"])
            live_milestones: list[tuple[str, float]] = []

            for milestone in candidate["due_milestones"]:
                target = published_at + delays[milestone]
                lag = max(0.0, (now - target).total_seconds())
                tolerance = MILESTONE_TOLERANCE[milestone].total_seconds()
                if lag > tolerance:
                    inserted = self.analytics.append(
                        publication_id=publication_id,
                        platform=str(candidate["platform"]),
                        milestone=milestone,
                        collector_version="missed-v1",
                        metrics={
                            "collection_status": "missed",
                            "capture_lag_seconds": int(lag),
                            "reason": "analytics_worker_outside_milestone_window",
                        },
                        captured_at=now,
                    )
                    if inserted:
                        stats["missed"] += 1
                else:
                    live_milestones.append((milestone, lag))

            if not live_milestones:
                continue

            publication = self.content_store.get_publication(publication_id)
            if publication is None:
                stats["failed"] += len(live_milestones)
                continue
            account = self.content_store.get_account(int(candidate["account_id"]))
            if account is None:
                stats["failed"] += len(live_milestones)
                continue

            if not publication.external_post_id:
                for milestone, lag in live_milestones:
                    if self.analytics.append(
                        publication_id=publication_id,
                        platform=publication.platform,
                        milestone=milestone,
                        collector_version="missing-remote-id-v1",
                        metrics={
                            "collection_status": "unavailable",
                            "capture_lag_seconds": int(lag),
                            "reason": "publication_has_no_external_post_id",
                        },
                        captured_at=now,
                    ):
                        stats["missed"] += 1
                continue

            try:
                metrics = collector.collect(
                    publication,
                    account_options=account.get("options") or {},
                )
            except Exception:
                # Keep the milestone open while it is still inside its allowed
                # collection window. A later worker pass may succeed after a
                # transient provider/auth issue; no fake snapshot is written.
                stats["failed"] += len(live_milestones)
                continue

            for milestone, lag in live_milestones:
                payload = dict(metrics)
                payload["capture_lag_seconds"] = int(lag)
                payload["milestone"] = milestone
                if self.analytics.append(
                    publication_id=publication_id,
                    platform=publication.platform,
                    milestone=milestone,
                    collector_version=collector.version,
                    metrics=payload,
                    captured_at=now,
                ):
                    stats["collected"] += 1

        return stats

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
