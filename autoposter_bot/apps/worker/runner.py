from __future__ import annotations

import os
import time

from autoposter_bot.application.content_factory_feedback import ContentFactoryFeedback
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
    feedback = ContentFactoryFeedback(
        store,
        endpoint=os.getenv("CONTENT_FACTORY_PERFORMANCE_URL", ""),
        token=os.getenv("CONTENT_FACTORY_PERFORMANCE_TOKEN", ""),
    )
    feedback.init_schema()

    interval = max(1, int(os.getenv("AUTOPOSTER_WORKER_INTERVAL_SECONDS", "5")))
    batch_size = max(1, int(os.getenv("AUTOPOSTER_WORKER_BATCH_SIZE", "25")))
    feedback_batch = max(1, int(os.getenv("CONTENT_FACTORY_FEEDBACK_BATCH_SIZE", "50")))

    while True:
        stats = worker.run_once(limit=batch_size)
        feedback_stats = feedback.dispatch(limit=feedback_batch)
        if stats["claimed"] or stats["recovered"]:
            print(f"[content-worker] {stats}")
        if feedback_stats["sent"] or feedback_stats["failed"]:
            print(f"[content-factory-feedback] {feedback_stats}")
        time.sleep(interval)


if __name__ == "__main__":
    main()
