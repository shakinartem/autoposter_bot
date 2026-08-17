"""Autoposter API composition root with Content Factory integration."""

from autoposter_bot.apps.api.content_factory_integration import init_content_factory_bridge, router
from autoposter_bot.apps.api.main import app

# Receipt/link schema is independent from Content OS tables and uses CREATE IF NOT EXISTS.
# Fail fast at API startup if the bridge migration is unavailable.
init_content_factory_bridge()
app.include_router(router)

__all__ = ["app"]
