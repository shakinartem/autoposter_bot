from __future__ import annotations

from dataclasses import dataclass

from autoposter_bot.models import PostJob, Target


@dataclass(slots=True)
class PublishResult:
    platform: str
    destination: str | None
    ok: bool
    detail: str


class Publisher:
    platform: str

    def publish(self, job: PostJob, target: Target, dry_run: bool = False) -> PublishResult:
        raise NotImplementedError
