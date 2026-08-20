from __future__ import annotations

from datetime import datetime

import requests

from autoposter_bot.application.publishing import PublishingApplication
from autoposter_bot.application.reconciliation import PublicationReconciler
from autoposter_bot.domain.content import ContentItem, MediaAsset, PlatformVariant, Publication, PublicationStatus
from autoposter_bot.infrastructure.content_store import SQLiteContentStore
from autoposter_bot.infrastructure.publication_tracking_schema import init_publication_tracking_schema
from autoposter_bot.models import Target
from autoposter_bot.platforms.legacy import LegacyPublisherAdapter
from autoposter_bot.platforms.registry import PlatformRegistry
from autoposter_bot.publishers.tiktok import TikTokPublisher


class FakeResponse:
    def __init__(self, payload: dict, *, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = str(payload)

    def json(self):
        return self._payload


def _target() -> Target:
    return Target(platform="tiktok", destination="creator", options={"access_token": "token"})


def test_tiktok_status_processing_keeps_tracking_identity(monkeypatch):
    monkeypatch.setattr(
        requests,
        "post",
        lambda *args, **kwargs: FakeResponse({
            "data": {"status": "PROCESSING_UPLOAD", "uploaded_bytes": 123},
            "error": {"code": "ok"},
        }),
    )
    result = TikTokPublisher().fetch_status("v_pub_file~v2.123", _target())

    assert result.ok is True
    assert result.status == "processing"
    assert result.provider_tracking_id == "v_pub_file~v2.123"
    assert result.external_post_id is None


def test_tiktok_status_publish_complete_returns_final_public_post_id(monkeypatch):
    monkeypatch.setattr(
        requests,
        "post",
        lambda *args, **kwargs: FakeResponse({
            "data": {
                "status": "PUBLISH_COMPLETE",
                "publicaly_available_post_id": [7488123456789012345],
            },
            "error": {"code": "ok"},
        }),
    )
    result = TikTokPublisher().fetch_status("v_pub_file~v2.123", _target())

    assert result.ok is True
    assert result.status == "published"
    assert result.provider_tracking_id == "v_pub_file~v2.123"
    assert result.external_post_id == "7488123456789012345"


def test_tiktok_status_provider_failure_marks_republish_recommendation(monkeypatch):
    monkeypatch.setattr(
        requests,
        "post",
        lambda *args, **kwargs: FakeResponse({
            "data": {"status": "FAILED", "fail_reason": "video_pull_failed"},
            "error": {"code": "ok"},
        }),
    )
    result = TikTokPublisher().fetch_status("v_pub_url~v2.123", _target())

    assert result.ok is False
    assert result.status == "failed"
    assert result.error_code == "tiktok_video_pull_failed"
    assert result.retryable is True


def _registry() -> PlatformRegistry:
    registry = PlatformRegistry()
    registry.register(LegacyPublisherAdapter(TikTokPublisher()))
    return registry


def test_publishing_reconcile_promotes_processing_to_published(monkeypatch):
    monkeypatch.setattr(
        requests,
        "post",
        lambda *args, **kwargs: FakeResponse({
            "data": {
                "status": "PUBLISH_COMPLETE",
                "publicaly_available_post_id": [7488123456789012345],
            },
            "error": {"code": "ok"},
        }),
    )
    publication = Publication(
        variant_id="variant-1",
        platform="tiktok",
        account_id=1,
        status=PublicationStatus.PROCESSING,
        provider_tracking_id="v_pub_file~v2.123",
    )

    result = PublishingApplication(_registry()).reconcile(
        publication,
        account_options={"access_token": "token"},
    )

    assert result.status == "published"
    assert publication.status == PublicationStatus.PUBLISHED
    assert publication.external_post_id == "7488123456789012345"
    assert publication.published_at is not None


class ReconcileStore:
    def __init__(self, publication: Publication):
        self.publication = publication
        self.account = {"id": 1, "platform": "tiktok", "options": {"access_token": "token"}}

    def list_publications(self, *, status=None, limit=100):
        return [self.publication] if self.publication.status == status else []

    def get_account(self, account_id):
        return self.account if account_id == 1 else None

    def save_publication(self, publication):
        self.publication = publication
        return publication


class ReconcilePublishing:
    def reconcile(self, publication, *, account_options):
        publication.status = PublicationStatus.PUBLISHED
        publication.external_post_id = "final-77"
        publication.published_at = datetime(2026, 8, 18, 12, 30, 0)
        from autoposter_bot.platforms.base import PublicationResult
        return PublicationResult(
            ok=True,
            status="published",
            provider_tracking_id=publication.provider_tracking_id,
            external_post_id="final-77",
        )


def test_reconciler_only_checks_processing_or_trackable_quarantine():
    publication = Publication(
        variant_id="variant-1",
        platform="tiktok",
        account_id=1,
        status=PublicationStatus.PROCESSING,
        provider_tracking_id="tracking-77",
    )
    store = ReconcileStore(publication)
    stats = PublicationReconciler(store=store, publishing=ReconcilePublishing()).run_once()

    assert stats["candidates"] == 1
    assert stats["published"] == 1
    assert store.publication.status == PublicationStatus.PUBLISHED
    assert store.publication.external_post_id == "final-77"


def test_sqlite_store_persists_tracking_id_separately(tmp_path):
    store = SQLiteContentStore(tmp_path / "data" / "autoposter.db")
    store.init_schema()
    init_publication_tracking_schema(backend="sqlite", connect=store.connect)
    # Minimal account row is enough for the Content OS FK chain.
    with store.connect() as db:
        db.execute(
            "INSERT INTO users(telegram_user_id, username, full_name, is_registered, created_at) VALUES (?, ?, ?, ?, ?)",
            (991001, "owner", "Owner", 1, datetime.now().isoformat()),
        )
        owner_id = int(db.execute("SELECT id FROM users WHERE telegram_user_id = ?", (991001,)).fetchone()["id"])
        account_id = int(db.execute(
            "INSERT INTO accounts(owner_user_id, name, platform, destination, options_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (owner_id, "TikTok", "tiktok", "creator", "{}", datetime.now().isoformat()),
        ).lastrowid)

    content = ContentItem(title="T", body="B")
    store.save_content(content)
    variant = PlatformVariant(
        content_id=content.id,
        platform="tiktok",
        text="hello",
        media=[MediaAsset(source="https://example.test/video.mp4", media_type="video")],
    )
    store.save_variant(variant)
    publication = Publication(
        variant_id=variant.id,
        platform="tiktok",
        account_id=account_id,
        status=PublicationStatus.PROCESSING,
        provider_tracking_id="v_pub_file~v2.sqlite",
    )
    store.save_publication(publication)

    loaded = store.get_publication(publication.id)
    assert loaded is not None
    assert loaded.provider_tracking_id == "v_pub_file~v2.sqlite"
    assert loaded.external_post_id is None
    assert loaded.status == PublicationStatus.PROCESSING


def test_tiktok_local_upload_checkpoints_tracking_before_ambiguous_upload_failure(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video-bytes")
    publisher = TikTokPublisher()
    monkeypatch.setattr(
        publisher,
        "_query_creator_info",
        lambda requests_module, access_token: {"privacy_level_options": ["SELF_ONLY"]},
    )
    init_calls = []

    def fake_init(**kwargs):
        init_calls.append(kwargs)
        return FakeResponse({
            "data": {
                "publish_id": "v_pub_file~checkpoint",
                "upload_url": "https://upload.example.test/video",
            },
            "error": {"code": "ok"},
        })

    monkeypatch.setattr(publisher, "_post_init", fake_init)
    monkeypatch.setattr(
        publisher,
        "_upload_binary",
        lambda **kwargs: (_ for _ in ()).throw(requests.exceptions.ConnectionError("response lost")),
    )
    progress = []
    from autoposter_bot.models import MediaItem, PostJob
    result = publisher.publish(
        PostJob(
            post_id="post-1",
            content_type="tiktok_video",
            text="hello",
            media_items=[MediaItem(str(video), "video")],
            targets=[],
        ),
        Target(platform="tiktok", destination="creator", options={"access_token": "token"}),
        progress_callback=progress.append,
    )

    assert len(init_calls) == 1
    assert progress[0].provider_tracking_id == "v_pub_file~checkpoint"
    assert progress[0].status == "processing"
    assert result.ok is False


def test_post_init_failure_with_tracking_stays_processing_until_reconciled():
    from autoposter_bot.platforms.base import PublicationResult

    class FailingAfterTrackingAdapter:
        platform = "tiktok"
        def capabilities(self):
            raise NotImplementedError
        def validate(self, variant):
            return []
        def publish(self, variant, publication, *, account_options, dry_run=False, progress_callback=None):
            progress = PublicationResult(ok=True, status="processing", provider_tracking_id="track-1")
            if progress_callback:
                progress_callback(progress)
            return PublicationResult(
                ok=False,
                status="failed",
                error_code="transient_platform_error",
                error_message="upload response lost",
                retryable=True,
            )

    registry = PlatformRegistry()
    registry.register(FailingAfterTrackingAdapter())
    publication = Publication(variant_id="v1", platform="tiktok", account_id=1)
    variant = PlatformVariant(content_id="c1", platform="tiktok", id="v1", text="hello")

    def checkpoint(progress):
        publication.provider_tracking_id = progress.provider_tracking_id
        publication.status = PublicationStatus.PROCESSING

    result = PublishingApplication(registry).publish(
        variant, publication, account_options={}, progress_callback=checkpoint
    )

    assert result.ok is False
    assert publication.provider_tracking_id == "track-1"
    assert publication.status == PublicationStatus.PROCESSING
    assert publication.metadata["post_init_error"]["code"] == "transient_platform_error"
