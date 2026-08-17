from __future__ import annotations

from contextlib import asynccontextmanager

from autoposter_bot.application.media_publishing import MediaResolvingPublishingApplication
from autoposter_bot.apps.api.content_factory_integration import ContentFactoryIntegration
from autoposter_bot.apps.api import main as base_main

app = base_main.app
persistence = base_main.persistence
media_storage = base_main.media_storage
registry = base_main.registry

# Existing API endpoints look up this module-global publisher at call time, so replacing
# it here upgrades direct API publishing without copying the large base API module.
base_main.publishing_application = MediaResolvingPublishingApplication(registry, media_storage)

integration = ContentFactoryIntegration(
    persistence=persistence,
    media_storage=media_storage,
    registry=registry,
)
original_lifespan = app.router.lifespan_context


@asynccontextmanager
async def integrated_lifespan(application):
    async with original_lifespan(application):
        integration.init_schema()
        yield


app.router.lifespan_context = integrated_lifespan
app.include_router(integration.router)

__all__ = ["app", "integration"]
