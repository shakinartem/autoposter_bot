from contextlib import contextmanager

from autoposter_bot.application.media_publishing import MediaResolvingPublishingApplication
from autoposter_bot.domain.content import MediaAsset, PlatformVariant, Publication
from autoposter_bot.platforms.base import CapabilitySpec, PlatformAdapter, PublicationResult
from autoposter_bot.platforms.registry import PlatformRegistry


class RecordingAdapter(PlatformAdapter):
    platform = "telegram"

    def capabilities(self):
        return CapabilitySpec(platform="telegram", content_types=("post",))

    def validate(self, variant):
        return []

    def publish(self, variant, publication, *, account_options, dry_run=False):
        assert variant.media[0].source == "/tmp/resolved.jpg"
        return PublicationResult(ok=True, status="published", external_post_id="1")


class RecordingStorage:
    @contextmanager
    def resolve_variant(self, variant, platform):
        assert platform == "telegram"
        yield PlatformVariant(
            id=variant.id,
            content_id=variant.content_id,
            platform=variant.platform,
            title=variant.title,
            text=variant.text,
            media=[MediaAsset(id="m1", source="/tmp/resolved.jpg", media_type="image")],
            fields=variant.fields,
            sync_with_master=variant.sync_with_master,
            revision=variant.revision,
            metadata=variant.metadata,
        )


def test_publishing_resolves_storage_before_adapter():
    registry = PlatformRegistry()
    registry.register(RecordingAdapter())
    app = MediaResolvingPublishingApplication(registry, RecordingStorage())
    variant = PlatformVariant(content_id="c1", platform="telegram", media=[MediaAsset(id="m1", source="s3://bucket/key.jpg", media_type="image")])
    publication = Publication(variant_id=variant.id, platform="telegram", account_id=1)
    result = app.publish(variant, publication, account_options={})
    assert result.ok is True
    assert publication.external_post_id == "1"
