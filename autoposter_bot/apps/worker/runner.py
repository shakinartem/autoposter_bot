from __future__ import annotations

import os
import time

from autoposter_bot.application.media_publishing import MediaResolvingPublishingApplication
from autoposter_bot.application.retry import PublicationRetryPolicy
from autoposter_bot.apps.worker.scheduler import PublicationWorker
from autoposter_bot.config import load_settings
from autoposter_bot.infrastructure.media_storage import build_media_storage
from autoposter_bot.infrastructure.persistence import build_persistence
from autoposter_bot.integrations.credential_refresh import CredentialRefreshService
from autoposter_bot.integrations.instagram_oauth import InstagramOAuthProvider
from autoposter_bot.integrations.tiktok_oauth import TikTokOAuthProvider
from autoposter_bot.platforms.factory import build_default_platform_registry


def main() -> None:
    settings = load_settings()
    persistence = build_persistence(settings)
    persistence.init_schema()
    media_storage = build_media_storage(settings)
    publishing = MediaResolvingPublishingApplication(build_default_platform_registry(settings), media_storage)
    credential_refresh = CredentialRefreshService(tiktok=TikTokOAuthProvider(settings), instagram=InstagramOAuthProvider())
    retry_policy = PublicationRetryPolicy(
        max_attempts=max(1, int(os.getenv("AUTOPOSTER_PUBLISH_MAX_ATTEMPTS", "5"))),
        base_delay_seconds=max(1, int(os.getenv("AUTOPOSTER_RETRY_BASE_SECONDS", "60"))),
        max_delay_seconds=max(1, int(os.getenv("AUTOPOSTER_RETRY_MAX_SECONDS", "3600"))),
        jitter_ratio=max(0.0, min(1.0, float(os.getenv("AUTOPOSTER_RETRY_JITTER_RATIO", "0.20")))),
    )
    worker = PublicationWorker(store=persistence.store, queue=persistence.queue, publishing=publishing, retry_policy=retry_policy, credential_refresh=credential_refresh)
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
