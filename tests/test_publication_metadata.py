from __future__ import annotations

from autoposter_bot.application.publishing import PublishingApplication
from autoposter_bot.domain.content import PlatformVariant, Publication
from autoposter_bot.platforms.base import CapabilitySpec, PlatformAdapter, PublicationResult
from autoposter_bot.platforms.registry import PlatformRegistry


class LegacyDetailAdapter(PlatformAdapter):
    def __init__(self, platform: str, detail: str) -> None:
        self.platform = platform
        self.detail = detail

    def capabilities(self) -> CapabilitySpec:
        return CapabilitySpec(platform=self.platform, content_types=("text",))

    def validate(self, variant):
        return []

    def publish(self, variant, publication, *, account_options, dry_run=False):
        return PublicationResult(
            ok=True,
            status="published",
            raw_response={"legacy_detail": self.detail},
        )


def _publish(platform: str, detail: str):
    registry = PlatformRegistry()
    registry.register(LegacyDetailAdapter(platform, detail))
    app = PublishingApplication(registry)
    variant = PlatformVariant(content_id="content-1", platform=platform, text="hello")
    publication = Publication(variant_id=variant.id, platform=platform, account_id=1)
    result = app.publish(variant, publication, account_options={})
    return result, publication


def test_instagram_legacy_result_recovers_remote_id():
    result, publication = _publish(
        "instagram",
        "Instagram publish succeeded (facebook_login): 17895695668004550",
    )
    assert result.external_post_id == "17895695668004550"
    assert publication.external_post_id == "17895695668004550"


def test_tiktok_legacy_result_recovers_tracking_id_without_faking_final_post_id():
    result, publication = _publish(
        "tiktok",
        "TikTok publish initialized: v_pub_file~v2.12345 (privacy=SELF_ONLY)",
    )
    assert result.provider_tracking_id == "v_pub_file~v2.12345"
    assert result.external_post_id is None
    assert publication.provider_tracking_id == "v_pub_file~v2.12345"
    assert publication.external_post_id is None
    assert publication.status.value == "processing"


def test_vk_legacy_result_recovers_post_id():
    result, publication = _publish("vk", "VK publish succeeded: 777")
    assert result.external_post_id == "777"
    assert publication.external_post_id == "777"
