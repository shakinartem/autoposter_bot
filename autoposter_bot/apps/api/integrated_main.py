from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from autoposter_bot.application.media_publishing import MediaResolvingPublishingApplication
from autoposter_bot.apps.api import main as base_main
from autoposter_bot.apps.api.content_factory_integration import ContentFactoryIntegration
from autoposter_bot.apps.api.runtime_security import validate_runtime_security


# Importing this composition root in production is fail-closed. The CLI runner also
# performs the same validation before Uvicorn imports application modules.
validate_runtime_security(require_content_factory=True)

app = base_main.app
persistence = base_main.persistence
media_storage = base_main.media_storage
registry = base_main.registry

# Direct API publication and scheduled publication resolve durable media identically.
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


def _allowed_hosts() -> list[str]:
    raw = os.getenv(
        "AUTOPOSTER_ALLOWED_HOSTS",
        "localhost,127.0.0.1,testserver,api,autoposter-api",
    )
    return [item.strip() for item in raw.split(",") if item.strip()]


# Keep the 0.12 API untouched and apply the public-server boundary at composition time.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts())


@app.middleware("http")
async def hardened_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if os.getenv("AUTOPOSTER_ENV", "development").strip().lower() in {"prod", "production"}:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.get("/ready")
def ready() -> dict[str, str]:
    try:
        with persistence.store.base.connect() as connection:
            connection.execute("SELECT 1").fetchone()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database is not ready") from exc
    return {
        "status": "ready",
        "service": "autoposter-api",
        "version": app.version,
        "database": persistence.backend,
    }


app.include_router(integration.router)

__all__ = ["app", "integration"]
