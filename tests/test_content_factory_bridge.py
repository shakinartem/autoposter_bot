import json

import pytest
from pydantic import ValidationError

from autoposter_bot.application.content_factory_bridge import ContentFactoryBridge, IdempotencyConflict
from autoposter_bot.apps.api.content_factory_contract import ContentPackageV1, package_hash
from autoposter_bot.infrastructure.content_store import SQLiteContentStore


class NoMediaIngestor:
    def ingest(self, ref):
        raise AssertionError("fixture does not contain media")

    @staticmethod
    def source_fingerprint(ref):
        return "unused"


def package_fixture(**canonical_overrides):
    canonical = {
        "content_type": "post",
        "topic": "Intent signals",
        "goal": "Educate",
        "title": "How intent becomes action",
        "body": "Signals only matter when they improve a future decision.",
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
        "variants": [
            {
                "platform": "telegram",
                "title": canonical["title"],
                "plain_text": canonical["body"],
                "cta": canonical["cta"],
                "hashtags": ["#intent"],
                "blocks": [],
                "media": [],
            }
        ],
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


def test_ingest_is_idempotent_and_converges_to_one_content_item(tmp_path):
    store = SQLiteContentStore(tmp_path / "autoposter.sqlite3")
    store.init_schema()
    bridge = ContentFactoryBridge(store, NoMediaIngestor())
    bridge.init_schema()

    first = bridge.ingest(package_fixture(), idempotency_key="delivery-1", supported_platforms={"telegram"})
    replay = bridge.ingest(package_fixture(), idempotency_key="delivery-1", supported_platforms={"telegram"})

    assert first["status"] == "accepted"
    assert replay["idempotent_replay"] is True
    assert replay["receipt_id"] == first["receipt_id"]
    assert replay["content_id"] == first["content_id"]
    assert len(store.list_content()) == 1
    assert len(store.list_variants(first["content_id"])) == 1

    with store.connect() as connection:
        receipts = connection.execute("SELECT COUNT(*) AS count FROM content_factory_receipts").fetchone()["count"]
        links = connection.execute("SELECT COUNT(*) AS count FROM content_factory_links").fetchone()["count"]
    assert receipts == 1
    assert links == 1


def test_same_idempotency_key_with_changed_payload_conflicts(tmp_path):
    store = SQLiteContentStore(tmp_path / "autoposter.sqlite3")
    store.init_schema()
    bridge = ContentFactoryBridge(store, NoMediaIngestor())
    bridge.init_schema()
    bridge.ingest(package_fixture(), idempotency_key="delivery-1", supported_platforms={"telegram"})

    with pytest.raises(IdempotencyConflict):
        bridge.ingest(package_fixture(body="A materially changed payload."), idempotency_key="delivery-1", supported_platforms={"telegram"})
