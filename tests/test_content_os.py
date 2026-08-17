from datetime import datetime

from autoposter_bot.application.publishing import PublishingApplication
from autoposter_bot.domain.content import PlatformVariant, Publication, PublicationStatus
from autoposter_bot.platforms.base import CapabilitySpec, PlatformAdapter, PublicationResult
from autoposter_bot.platforms.registry import PlatformRegistry


class FakeAdapter(PlatformAdapter):
    platform = "fake"

    def capabilities(self) -> CapabilitySpec:
        return CapabilitySpec(platform="fake", content_types=("text",))

    def validate(self, variant):
        return []

    def publish(self, variant, publication, *, account_options, dry_run=False):
        return PublicationResult(
            ok=True,
            status="validated" if dry_run else "published",
            external_post_id=None if dry_run else "remote-123",
            external_url=None if dry_run else "https://example.test/post/remote-123",
            published_at=None if dry_run else datetime(2026, 8, 17, 12, 0, 0),
        )


def build_app() -> PublishingApplication:
    registry = PlatformRegistry()
    registry.register(FakeAdapter())
    return PublishingApplication(registry)


def test_publication_has_independent_lifecycle():
    app = build_app()
    variant = PlatformVariant(content_id="content-1", platform="fake", text="Hello")
    publication = Publication(variant_id=variant.id, platform="fake", account_id=7)

    result = app.publish(variant, publication, account_options={})

    assert result.ok is True
    assert publication.status == PublicationStatus.PUBLISHED
    assert publication.external_post_id == "remote-123"
    assert publication.attempt_count == 1


def test_dry_run_does_not_mutate_publication_state():
    app = build_app()
    variant = PlatformVariant(content_id="content-1", platform="fake", text="Hello")
    publication = Publication(variant_id=variant.id, platform="fake", account_id=7)

    result = app.publish(variant, publication, account_options={}, dry_run=True)

    assert result.ok is True
    assert result.status == "validated"
    assert publication.status == PublicationStatus.DRAFT
    assert publication.external_post_id is None
    assert publication.attempt_count == 0


def test_variant_override_breaks_master_sync():
    variant = PlatformVariant(content_id="content-1", platform="fake", text="Original")

    variant.override()

    assert variant.sync_with_master is False
    assert variant.revision == 2
