from __future__ import annotations

from types import SimpleNamespace

from autoposter_bot.analytics.instagram import InstagramPerformanceCollector
from autoposter_bot.domain.content import Publication, PublicationStatus


class Response:
    def __init__(self, payload, *, ok=True):
        self.payload = payload
        self.ok = ok

    def json(self):
        return self.payload


def test_instagram_collector_merges_basic_counts_and_insights(monkeypatch):
    settings = SimpleNamespace(instagram_graph_api_version="v22.0")
    collector = InstagramPerformanceCollector(settings)
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/insights"):
            return Response({
                "data": [
                    {"name": "views", "values": [{"value": 150}]},
                    {"name": "reach", "total_value": {"value": 90}},
                    {"name": "saved", "values": [{"value": "7"}]},
                ]
            })
        return Response({
            "id": "media-1",
            "media_type": "IMAGE",
            "like_count": 12,
            "comments_count": 3,
            "timestamp": "2026-08-18T10:00:00+0000",
            "permalink": "https://instagram.example/p/1",
        })

    monkeypatch.setattr("autoposter_bot.analytics.instagram.requests.get", fake_get)
    publication = Publication(
        variant_id="variant-1",
        platform="instagram",
        account_id=5,
        status=PublicationStatus.PUBLISHED,
        external_post_id="media-1",
    )

    metrics = collector.collect(
        publication,
        account_options={
            "access_token": "super-secret-token",
            "api_flow": "instagram_login",
        },
    )

    assert metrics["like_count"] == 12
    assert metrics["comments_count"] == 3
    assert metrics["views"] == 150
    assert metrics["reach"] == 90
    assert metrics["saved"] == 7
    assert calls[0][0].startswith("https://graph.instagram.com/v22.0/")
    assert "super-secret-token" not in str(metrics)


def test_instagram_collector_keeps_basic_metrics_when_insights_are_unsupported(monkeypatch):
    settings = SimpleNamespace(instagram_graph_api_version="v22.0")
    collector = InstagramPerformanceCollector(settings)
    counter = {"value": 0}

    def fake_get(url, **kwargs):
        counter["value"] += 1
        if url.endswith("/insights"):
            return Response({"error": {"code": 100, "message": "Metric not supported"}}, ok=False)
        return Response({
            "id": "media-2",
            "media_type": "CAROUSEL_ALBUM",
            "like_count": 30,
            "comments_count": 4,
        })

    monkeypatch.setattr("autoposter_bot.analytics.instagram.requests.get", fake_get)
    publication = Publication(
        variant_id="variant-2",
        platform="instagram",
        account_id=6,
        status=PublicationStatus.PUBLISHED,
        external_post_id="media-2",
    )

    metrics = collector.collect(
        publication,
        account_options={"access_token": "secret", "api_flow": "instagram_login"},
    )

    assert metrics["like_count"] == 30
    assert metrics["comments_count"] == 4
    assert metrics["insights_status"] == "partial"
    assert "Metric not supported" in metrics["insights_error"]
    assert counter["value"] == 2
