from __future__ import annotations

import os
from typing import Any

import requests

from autoposter_bot.config import Settings
from autoposter_bot.domain.content import Publication


class InstagramPerformanceCollector:
    platform = "instagram"
    version = "instagram-v1"

    def __init__(self, settings: Settings) -> None:
        self.api_version = settings.instagram_graph_api_version
        configured = os.getenv(
            "INSTAGRAM_ANALYTICS_METRICS",
            "views,reach,saved,shares,total_interactions",
        )
        self.insight_metrics = tuple(
            item.strip() for item in configured.split(",") if item.strip()
        )

    def collect(
        self,
        publication: Publication,
        *,
        account_options: dict[str, Any],
    ) -> dict[str, Any]:
        media_id = str(publication.external_post_id or "").strip()
        token = str(account_options.get("access_token") or "").strip()
        if not media_id:
            raise ValueError("Instagram publication has no external media id")
        if not token:
            raise ValueError("Instagram account has no access token")

        flow = str(account_options.get("api_flow") or "instagram_login").strip().lower()
        base = self._base_url(flow)
        auth_params = {"access_token": token}

        media_response = requests.get(
            f"{base}/{media_id}",
            params={
                **auth_params,
                "fields": "id,media_type,timestamp,permalink,like_count,comments_count",
            },
            timeout=30,
        )
        media_payload = self._json(media_response)
        if not media_response.ok or media_payload.get("error"):
            raise RuntimeError(
                f"Instagram media metrics failed: {self._safe_error(media_payload)}"
            )

        metrics: dict[str, Any] = {
            "collection_status": "ok",
            "like_count": self._number(media_payload.get("like_count")),
            "comments_count": self._number(media_payload.get("comments_count")),
            "media_type": media_payload.get("media_type"),
            "media_timestamp": media_payload.get("timestamp"),
        }

        if self.insight_metrics:
            insight_response = requests.get(
                f"{base}/{media_id}/insights",
                params={
                    **auth_params,
                    "metric": ",".join(self.insight_metrics),
                },
                timeout=30,
            )
            insight_payload = self._json(insight_response)
            if insight_response.ok and not insight_payload.get("error"):
                metrics.update(self._flatten_insights(insight_payload.get("data") or []))
            else:
                # Basic media counts are still valuable. Capture a sanitized
                # diagnostic instead of dropping the whole snapshot because a
                # media type/API version does not expose one configured insight.
                metrics["insights_status"] = "partial"
                metrics["insights_error"] = self._safe_error(insight_payload)

        return metrics

    def _base_url(self, flow: str) -> str:
        host = "graph.instagram.com" if flow == "instagram_login" else "graph.facebook.com"
        return f"https://{host}/{self.api_version}"

    @staticmethod
    def _flatten_insights(rows: list[dict[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for row in rows:
            name = str(row.get("name") or row.get("title") or "").strip()
            if not name:
                continue
            value: Any = None
            total_value = row.get("total_value")
            if isinstance(total_value, dict) and "value" in total_value:
                value = total_value.get("value")
            elif row.get("values") and isinstance(row["values"], list):
                latest = row["values"][-1] if row["values"] else {}
                if isinstance(latest, dict):
                    value = latest.get("value")
            elif "value" in row:
                value = row.get("value")
            if value is not None:
                output[name] = InstagramPerformanceCollector._number(value)
        return output

    @staticmethod
    def _number(value: Any) -> Any:
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, (int, float)):
            return value
        try:
            numeric = float(str(value))
        except (TypeError, ValueError):
            return value
        return int(numeric) if numeric.is_integer() else numeric

    @staticmethod
    def _json(response: requests.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("Instagram analytics returned non-JSON response") from exc
        return payload if isinstance(payload, dict) else {"data": payload}

    @staticmethod
    def _safe_error(payload: dict[str, Any]) -> str:
        error = payload.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            message = error.get("message")
            return f"[{code}] {message}"[:300]
        return str(error or "unsupported insights response")[:300]
