from __future__ import annotations

import os
import time

from autoposter_bot.application.publishing import PublishingApplication
from autoposter_bot.config import load_settings
from autoposter_bot.infrastructure.content_store import SQLiteContentStore
from autoposter_bot.infrastructure.publication_queue import SQLitePublicationQueue
from autoposter_bot.platforms.factory import build_default_platform_registry
from autoposter_bot.apps.worker.scheduler import PublicationWorker


def main() -> None:
    settings = load_settings()
    store = SQLiteContentStore(settings.database_path)
    store.init_schema()
    queue = SQLitePublicationQueue(settings.database_path)
    publishing = PublishingApplication(build_default_platform_registry(settings))
    worker = PublicationWorker(store=store, queue=queue, publishing=publishing)

    interval = max(1, int(os.getenv("AUTOPOSTER_WORKER_INTERVAL_SECONDS", "5")))
    batch_size = max(1, int(os.getenv("AUTOPOSTER_WORKER_BATCH_SIZE", "25")))

    while True:
        stats = worker.run_once(limit=batch_size)
        if stats["claimed"] or stats["recovered"]:
            print(f"[content-worker] {stats}")
        time.sleep(interval)


if __name__ == "__main__":
    main()
