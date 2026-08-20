"""FastAPI application package.

Import the composition root explicitly from ``autoposter_bot.apps.api.main``.
Keeping package import side-effect free avoids circular/partial initialization when
routers import schemas and security helpers from this package.
"""

__all__: list[str] = []
