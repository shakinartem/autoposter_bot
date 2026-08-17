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
            status="published",
            external_post_id="remote-123",
            external_url="https://example.test/post/remote-123",
            published_at=datetime(2026, 8, 17, 12, 0, 0),
        )


def test_publication_has_independent_lifecycle():
    registry = PlatformRegistry()
    registry.register(FakeAdapter())
    app = PublishingApplication(registry)

    variant = PlatformVariant(content_id="content-1", platform="fake", text="Hello")
    publication = Publication(variant_id=variant.id, platform="fake", account_id=7)

    result = app.publish(variant, publication, account_options={})

    assert result.ok is True
    assert publication.status == PublicationStatus.PUBLISHED
    assert publication.external_post_id == "remote-123"
    assert publication.attempt_count == 1


def test_variant_override_breaks_master_sync():
    variant = PlatformVariant(content_id="content-1", platform="fake", text="Original")

    variant.override()

    assert variant.sync_with_master is False
    assert variant.revision == 2
