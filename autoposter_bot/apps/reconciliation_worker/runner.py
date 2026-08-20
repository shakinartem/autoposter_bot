from __future__ import annotations

import os
import time

from autoposter_bot.application.publishing import PublishingApplication
from autoposter_bot.application.reconciliation import PublicationReconciler
from autoposter_bot.config import load_settings
from autoposter_bot.infrastructure.persistence import build_persistence
from autoposter_bot.integrations.credential_refresh import CredentialRefreshService
from autoposter_bot.integrations.instagram_oauth import InstagramOAuthProvider
from autoposter_bot.integrations.tiktok_oauth import TikTokOAuthProvider
from autoposter_bot.platforms.factory import build_default_platform_registry


def main() -> None:
    settings = load_settings()
    persistence = build_persistence(settings)
    persistence.init_schema()
    publishing = PublishingApplication(build_default_platform_registry(settings))
    credential_refresh = CredentialRefreshService(
        tiktok=TikTokOAuthProvider(settings),
        instagram=InstagramOAuthProvider(),
    )
    service = PublicationReconciler(
        store=persistence.store,
        publishing=publishing,
        credential_refresh=credential_refresh,
    )

    interval = max(10, int(os.getenv("AUTOPOSTER_RECONCILIATION_INTERVAL_SECONDS", "30")))
    batch_size = max(1, int(os.getenv("AUTOPOSTER_RECONCILIATION_BATCH_SIZE", "100")))
    try:
        while True:
            stats = service.run_once(limit=batch_size)
            if stats["candidates"]:
                print(f"[reconciliation-worker:{persistence.backend}] {stats}")
            time.sleep(interval)
    finally:
        persistence.close()


if __name__ == "__main__":
    main()
