from __future__ import annotations

import logging
import os
import time

from autoposter_bot.application.account_health import AccountHealthApplication
from autoposter_bot.config import load_settings
from autoposter_bot.infrastructure.persistence import build_persistence
from autoposter_bot.integrations.credential_refresh import CredentialRefreshService
from autoposter_bot.integrations.instagram_oauth import InstagramOAuthProvider
from autoposter_bot.integrations.tiktok_oauth import TikTokOAuthProvider


logger = logging.getLogger("autoposter.account_health_worker")


def main() -> None:
    logging.basicConfig(
        level=os.getenv("AUTOPOSTER_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = load_settings()
    persistence = build_persistence(settings)
    persistence.init_schema()
    tiktok = TikTokOAuthProvider(settings)
    instagram = InstagramOAuthProvider()
    refresh = CredentialRefreshService(tiktok=tiktok, instagram=instagram)
    application = AccountHealthApplication(
        account_health=persistence.account_health,
        store_for_workspace=persistence.scoped,
        credential_refresh=refresh,
        tiktok=tiktok,
        instagram=instagram,
        telegram_bot_token=settings.telegram_bot_token,
    )
    poll_seconds = max(300.0, float(os.getenv("AUTOPOSTER_ACCOUNT_HEALTH_POLL_SECONDS", "900")))
    logger.info("account health worker started: poll=%ss", poll_seconds)
    try:
        while True:
            stats = application.run_once()
            logger.info(
                "account health scan workspaces=%s accounts=%s healthy=%s degraded=%s critical=%s",
                stats.workspaces,
                stats.accounts,
                stats.healthy,
                stats.degraded,
                stats.critical,
            )
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        logger.info("account health worker stopped")
    finally:
        persistence.close()


if __name__ == "__main__":
    main()
