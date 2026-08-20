from __future__ import annotations

import pytest
from pydantic import ValidationError

from autoposter_bot.application.content_factory_feedback import ContentFactoryFeedback
from autoposter_bot.apps.api.content_factory_contract import ContentPackageV1, package_hash
from autoposter_bot.infrastructure.content_factory_media import DurableMediaIngestor, MediaIngestError


def package_fixture(**canonical_overrides):
    canonical = {
        "content_type": "post",
        "topic": "Intent signals",
        "goal": "Educate",
        "title": "How intent becomes action",
        "body": "Signals matter when they improve a future decision.",
        "hook": "Not every lead is ready now.",
        "cta": "Review the signal before acting.",
    }
    canonical.update(canonical_overrides)
    return ContentPackageV1.model_validate({
        "schema_version": "content-package/1.0",
        "content_id": "11111111-1111-1111-1111-111111111111",
        "project_id": "22222222-2222-2222-2222-222222222222",
        "status": "approved",
        "canonical": canonical,
        "variants": [{
            "platform": "telegram",
            "title": canonical["title"],
            "plain_text": canonical["body"],
            "cta": canonical["cta"],
            "hashtags": ["#intent"],
            "blocks": [],
            "media": [],
        }],
        "sources": [],
        "quality": {"overall": 0.94, "factuality": 0.98, "brand_voice": 0.91, "media": None},
    })


def test_contract_is_strict_and_hash_is_stable():
    package = package_fixture()
    assert package_hash(package) == package_hash(package_fixture())
    payload = package.model_dump(mode="json")
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        ContentPackageV1.model_validate(payload)


def test_media_ssrf_defaults_are_strict():
    ingestor = DurableMediaIngestor(storage=None, ledger=None)  # validation does not touch dependencies
    with pytest.raises(MediaIngestError):
        ingestor._validate_url("http://example.com/a.jpg")
    with pytest.raises(MediaIngestError):
        ingestor._validate_url("https://127.0.0.1/a.jpg")


def test_local_dev_can_explicitly_allow_http_private_hosts():
    ingestor = DurableMediaIngestor(storage=None, ledger=None, allow_private_hosts=True)
    parsed = ingestor._validate_url("http://127.0.0.1:9000/a.jpg")
    assert parsed.hostname == "127.0.0.1"


def test_feedback_filters_non_numeric_analytics_metadata():
    cleaned = ContentFactoryFeedback._clean_metrics({
        "views": 100,
        "clicks": 4.0,
        "collection_status": "ok",
        "milestone": "1h",
    })
    assert cleaned == {"views": 100, "clicks": 4}


def test_contract_v11_requires_lineage_and_preserves_it():
    payload = package_fixture().model_dump(mode="json")
    payload["schema_version"] = "content-package/1.1"
    with pytest.raises(ValidationError):
        ContentPackageV1.model_validate(payload)
    payload["lineage"] = {
        "content_version": 4,
        "export_delivery_id": "33333333-3333-3333-3333-333333333333",
        "generation_run_id": "44444444-4444-4444-4444-444444444444",
        "prompt_version": "factory-v4",
        "prompt_hash": "a" * 64,
        "model": "gpt-test",
        "model_router": {"reason": "shadow_only"},
        "shadow_experiment_ids": [],
    }
    package = ContentPackageV1.model_validate(payload)
    assert package.lineage is not None
    assert package.lineage.content_version == 4
    assert package.lineage.prompt_hash == "a" * 64


def test_feedback_rejects_non_finite_negative_and_extreme_metrics():
    for value in (float("nan"), float("inf"), -1, 1e16):
        with pytest.raises(ValueError):
            ContentFactoryFeedback._clean_metrics({"views": value})


def test_image_signature_validation_rejects_mismatched_bytes():
    import io

    with pytest.raises(MediaIngestError):
        DurableMediaIngestor._validate_image_signature(io.BytesIO(b"not-a-png"), "image/png")
    DurableMediaIngestor._validate_image_signature(io.BytesIO(b"\x89PNG\r\n\x1a\nrest"), "image/png")
