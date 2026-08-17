from __future__ import annotations

import os
import time

from autoposter_bot.application.publishing import PublishingApplication
from autoposter_bot.apps.worker.scheduler import PublicationWorker
from autoposter_bot.config import load_settings
from autoposter_bot.infrastructure.persistence import build_persistence
from autoposter_bot.platforms.factory import build_default_platform_registry


def main() -> None:
    settings = load_settings()
    persistence = build_persistence(settings)
    persistence.init_schema()
    publishing = PublishingApplication(build_default_platform_registry(settings))
    worker = PublicationWorker(
        store=persistence.store,
        queue=persistence.queue,
        publishing=publishing,
    )

    interval = max(1, int(os.getenv("AUTOPOSTER_WORKER_INTERVAL_SECONDS", "5")))
    batch_size = max(1, int(os.getenv("AUTOPOSTER_WORKER_BATCH_SIZE", "25")))

    try:
        while True:
            stats = worker.run_once(limit=batch_size)
            if stats["claimed"] or stats["recovered"]:
                print(f"[content-worker:{persistence.backend}] {stats}")
            time.sleep(interval)
    finally:
        persistence.close()


if __name__ == "__main__":
    main()
