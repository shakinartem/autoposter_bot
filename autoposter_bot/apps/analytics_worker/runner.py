from __future__ import annotations

import os
import time

from autoposter_bot.analytics.base import CollectorRegistry
from autoposter_bot.analytics.instagram import InstagramPerformanceCollector
from autoposter_bot.application.analytics import AnalyticsCollectionService
from autoposter_bot.config import load_settings
from autoposter_bot.infrastructure.analytics_store import AnalyticsStore
from autoposter_bot.infrastructure.persistence import build_persistence


def main() -> None:
    settings = load_settings()
    persistence = build_persistence(settings)
    persistence.init_schema()
    analytics = AnalyticsStore(
        backend=persistence.backend,
        connect=persistence.store.base.connect,
    )
    analytics.init_schema()
    collectors = CollectorRegistry([
        InstagramPerformanceCollector(settings),
    ])
    service = AnalyticsCollectionService(
        analytics=analytics,
        content_store=persistence.store,
        collectors=collectors,
    )

    interval = max(60, int(os.getenv("AUTOPOSTER_ANALYTICS_WORKER_INTERVAL_SECONDS", "300")))
    batch_size = max(1, int(os.getenv("AUTOPOSTER_ANALYTICS_BATCH_SIZE", "250")))

    try:
        while True:
            stats = service.run_once(limit=batch_size)
            if stats["candidates"]:
                print(f"[analytics-worker:{persistence.backend}] {stats}")
            time.sleep(interval)
    finally:
        persistence.close()


if __name__ == "__main__":
    main()
