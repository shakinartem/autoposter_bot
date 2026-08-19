from __future__ import annotations

import logging
import os
import time

from autoposter_bot.application.health_alerts import HealthAlertApplication
from autoposter_bot.config import load_settings
from autoposter_bot.infrastructure.persistence import build_persistence
from autoposter_bot.notifier import TelegramNotifier


logger = logging.getLogger("autoposter.health_worker")


def main() -> None:
    logging.basicConfig(
        level=os.getenv("AUTOPOSTER_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = load_settings()
    persistence = build_persistence(settings)
    persistence.init_schema()
    notifier = TelegramNotifier(settings)
    application = HealthAlertApplication(
        operations=persistence.operations,
        alerts=persistence.health_alerts,
        auth=persistence.auth,
        notifier=notifier,
        minimum_severity=os.getenv("AUTOPOSTER_HEALTH_ALERT_MINIMUM_SEVERITY", "critical").strip().lower(),
        reminder_seconds=int(os.getenv("AUTOPOSTER_HEALTH_ALERT_REMINDER_SECONDS", "21600")),
    )
    poll_seconds = max(30.0, float(os.getenv("AUTOPOSTER_HEALTH_POLL_SECONDS", "60")))
    logger.info("health alert worker started: poll=%ss notifier=%s", poll_seconds, notifier.is_bot_configured())
    try:
        while True:
            stats = application.run_once()
            logger.info(
                "health scan workspaces=%s active=%s notifications=%s resolved=%s suppressed=%s undeliverable=%s delivery_failures=%s",
                stats.workspaces,
                stats.active,
                stats.notifications,
                stats.resolved_notifications,
                stats.suppressed,
                stats.undeliverable,
                stats.delivery_failures,
            )
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        logger.info("health alert worker stopped")
    finally:
        persistence.close()


if __name__ == "__main__":
    main()
