from __future__ import annotations

from autoposter_bot.platforms.base import PlatformAdapter


class PlatformRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, PlatformAdapter] = {}

    def register(self, adapter: PlatformAdapter) -> None:
        key = adapter.platform.strip().lower()
        if not key:
            raise ValueError("Platform adapter must define a non-empty platform")
        self._adapters[key] = adapter

    def get(self, platform: str) -> PlatformAdapter:
        key = platform.strip().lower()
        try:
            return self._adapters[key]
        except KeyError as exc:
            raise KeyError(f"Platform adapter is not registered: {platform}") from exc

    def capabilities(self) -> dict[str, object]:
        return {name: adapter.capabilities() for name, adapter in self._adapters.items()}

    def platforms(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))
