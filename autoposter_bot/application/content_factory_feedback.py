from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import requests

from autoposter_bot.infrastructure.content_factory_ledger import ContentFactoryLedger


class FeedbackNotLinked(RuntimeError):
    pass


class ContentFactoryFeedback:
    def __init__(
        self,
        ledger: ContentFactoryLedger,
        *,
        endpoint: str = "",
        token: str = "",
        timeout_seconds: float = 20.0,
    ) -> None:
        self.ledger = ledger
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def record_snapshot(
        self,
        publication_id: str,
        *,
        metrics: dict[str, int | float],
        captured_at: datetime | None = None,
        event_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_metrics = self._clean_metrics(metrics)
        captured = (captured_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        event = event_id or f"autoposter-{uuid4()}"
        context = self.ledger.publication_context(publication_id)
        if not context["source_content_id"]:
            raise FeedbackNotLinked("Publication does not originate from Content Factory")
        callback_metadata = {
            "publication_id": publication_id,
            "external_url": context["external_url"],
            **(metadata or {}),
        }
        payload = {
            "event_id": event,
            "source": "autoposter",
            "content_id": context["source_content_id"],
            "external_publication_id": context["external_publication_id"],
            "platform": context["platform"],
            "captured_at": captured.isoformat().replace("+00:00", "Z"),
            "metrics": clean_metrics,
            "metadata": callback_metadata,
        }
        return self.ledger.record_feedback(
            publication_id=publication_id,
            event_id=event,
            platform=context["platform"],
            metrics=clean_metrics,
            metadata=callback_metadata,
            captured_at=captured,
            source_content_id=context["source_content_id"],
            external_publication_id=context["external_publication_id"],
            payload=payload,
        )

    def dispatch(self, *, limit: int = 50) -> dict[str, int]:
        if not self.endpoint or not self.token:
            return {"sent": 0, "failed": 0, "disabled": 1}
        rows = self.ledger.claim_feedback(limit=limit)
        sent = failed = 0
        for row in rows:
            try:
                response = requests.post(
                    self.endpoint,
                    json=row["payload_json"],
                    headers={"X-Performance-Token": self.token},
                    timeout=self.timeout_seconds,
                )
                if response.status_code not in {200, 202}:
                    raise RuntimeError(f"Factory returned HTTP {response.status_code}: {response.text[:500]}")
                self.ledger.mark_feedback_sent(row["id"])
                sent += 1
            except Exception as exc:
                self.ledger.mark_feedback_failed(row["id"], attempts=int(row["attempts"]), error=str(exc))
                failed += 1
        return {"sent": sent, "failed": failed, "disabled": 0}

    @staticmethod
    def _clean_metrics(metrics: dict[str, int | float]) -> dict[str, int | float]:
        clean: dict[str, int | float] = {}
        for key, raw in metrics.items():
            name = key.strip().lower()
            if not name:
                continue
            value = float(raw)
            if value < 0:
                raise ValueError(f"Metric {name} cannot be negative")
            clean[name] = int(value) if value.is_integer() else value
        if not clean:
            raise ValueError("At least one numeric metric is required")
        return clean
