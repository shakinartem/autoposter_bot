from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "autoposter_bot.apps.api.main:app",
        host=os.getenv("AUTOPOSTER_API_HOST", "0.0.0.0"),
        port=int(os.getenv("AUTOPOSTER_API_PORT", "8000")),
        reload=os.getenv("AUTOPOSTER_API_RELOAD", "0").strip().lower() in {"1", "true", "yes"},
    )


if __name__ == "__main__":
    main()
