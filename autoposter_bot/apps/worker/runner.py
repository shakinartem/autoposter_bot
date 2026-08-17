from __future__ import annotations

import os
import time

from autoposter_bot.application.content_factory_feedback import ContentFactoryFeedback
from autoposter_bot.application.media_publishing import MediaResolvingPublishingApplication
from autoposter_bot.apps.worker.scheduler import PublicationWorker
from autoposter_bot.config import load_settings
from autoposter_bot.infrastructure.content_factory_ledger import ContentFactoryLedger
from autoposter_bot.infrastructure.media_storage import build_media_storage
from autoposter_bot.infrastructure.persistence import build_persistence
from autoposter_bot.platforms.factory import build_default_platform_registry


def main() -> None:
    settings = load_settings()
    persistence = build_persistence(settings)
    persistence.init_schema()
    ledger = ContentFactoryLedger(persistence)
    ledger.init_schema()
    media_storage = build_media_storage(settings)
    publishing = MediaResolvingPublishingApplication(build_default_platform_registry(settings), media_storage)
    worker = PublicationWorker(store=persistence.store, queue=persistence.queue, publishing=publishing)
    feedback = ContentFactoryFeedback(
        ledger,
        endpoint=os.getenv("CONTENT_FACTORY_PERFORMANCE_URL", ""),
        token=os.getenv("CONTENT_FACTORY_PERFORMANCE_TOKEN", ""),
    )
    interval = max(1, int(os.getenv("AUTOPOSTER_WORKER_INTERVAL_SECONDS", "5")))
    batch_size = max(1, int(os.getenv("AUTOPOSTER_WORKER_BATCH_SIZE", "25")))
    feedback_batch = max(1, int(os.getenv("CONTENT_FACTORY_FEEDBACK_BATCH_SIZE", "50")))
    try:
        while True:
            stats = worker.run_once(limit=batch_size)
            feedback_stats = feedback.dispatch(limit=feedback_batch)
            if stats["claimed"] or stats["recovered"]:
                print(f"[content-worker:{persistence.backend}] {stats}")
            if feedback_stats["sent"] or feedback_stats["failed"]:
                print(f"[content-factory-feedback:{persistence.backend}] {feedback_stats}")
            time.sleep(interval)
    finally:
        persistence.close()


if __name__ == "__main__":
    main()
