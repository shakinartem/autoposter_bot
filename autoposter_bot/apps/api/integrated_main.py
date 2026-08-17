from __future__ import annotations

from contextlib import asynccontextmanager

from autoposter_bot.apps.api.content_factory_integration import ContentFactoryIntegration
from autoposter_bot.apps.api.main import app, media_storage, persistence, registry

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
