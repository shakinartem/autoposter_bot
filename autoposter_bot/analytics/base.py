from __future__ import annotations

from typing import Any, Protocol

from autoposter_bot.domain.content import Publication


class PerformanceCollector(Protocol):
    platform: str
    version: str

    def collect(
        self,
        publication: Publication,
        *,
        account_options: dict[str, Any],
    ) -> dict[str, Any]: ...


class CollectorRegistry:
    def __init__(self, collectors: list[PerformanceCollector] | None = None) -> None:
        self._collectors: dict[str, PerformanceCollector] = {}
        for collector in collectors or []:
            self.register(collector)

    def register(self, collector: PerformanceCollector) -> None:
        key = collector.platform.strip().lower()
        if not key:
            raise ValueError("Collector platform is required")
        self._collectors[key] = collector

    def get(self, platform: str) -> PerformanceCollector | None:
        return self._collectors.get(platform.strip().lower())

    def platforms(self) -> tuple[str, ...]:
        return tuple(sorted(self._collectors))
