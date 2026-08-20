from types import SimpleNamespace

from autoposter_bot.domain.content import MediaAsset, PlatformVariant, Publication
from autoposter_bot.models import MediaItem, PostJob, Target
from autoposter_bot.platforms.legacy import LegacyPublisherAdapter
from autoposter_bot.publishers.base import PublishResult, Publisher
from autoposter_bot.publishers.instagram import InstagramPublisher
from autoposter_bot.publishers.telegram import TelegramPublisher
from autoposter_bot.publishers.tiktok import TikTokPublisher


class CapturePublisher(Publisher):
    platform = "tiktok"

    def publish(self, job, target, dry_run=False):
        return PublishResult(self.platform, target.destination, True, "ok")


def test_tiktok_variant_compiles_to_legacy_content_type_and_options():
    adapter = LegacyPublisherAdapter(CapturePublisher())
    variant = PlatformVariant(
        content_id="content-1",
        platform="tiktok",
        text="Caption",
        media=[MediaAsset(source="https://cdn.example/video.mp4", media_type="video")],
        fields={
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "post_mode": "DIRECT_POST",
            "disable_comment": True,
        },
    )
    publication = Publication(variant_id=variant.id, platform="tiktok", account_id=9)

    job = adapter._to_legacy_job(variant, publication, {"access_token": "secret"})

    assert job.content_type == "tiktok_video"
    assert job.targets[0].options["access_token"] == "secret"
    assert job.targets[0].options["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert job.targets[0].options["disable_comment"] is True


def test_telegram_options_strip_plain_parse_mode():
    assert TelegramPublisher._message_options(
        {"parse_mode": "plain", "disable_notification": True}
    ) == {"disable_notification": True}
    assert TelegramPublisher._message_options({"parse_mode": "HTML"}) == {"parse_mode": "HTML"}


def test_instagram_reel_honors_share_to_feed_false():
    captured = {}

    class Response:
        def json(self):
            return {"id": "creation-1"}

    class Requests:
        @staticmethod
        def post(url, **kwargs):
            captured.update(kwargs["data"])
            return Response()

    publisher = InstagramPublisher()
    result = publisher._create_container(
        requests=Requests,
        base_url="https://graph.example",
        auth={"headers": {}, "params": {}, "data": {}},
        ig_user_id="ig-1",
        content_type="instagram_video",
        caption="Caption",
        media_items=[MediaItem("https://cdn.example/reel.mp4", "video")],
        flow="facebook_login",
        options={"share_to_feed": False},
    )

    assert result == "creation-1"
    assert captured["share_to_feed"] == "false"


def test_tiktok_user_privacy_overrides_global_default(monkeypatch):
    publisher = TikTokPublisher(None)
    publisher.settings = SimpleNamespace(tiktok_default_privacy_level="SELF_ONLY")
    captured = {}

    monkeypatch.setattr(
        publisher,
        "_query_creator_info",
        lambda requests, access_token: {
            "privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"],
            "comment_disabled": False,
            "duet_disabled": False,
            "stitch_disabled": False,
        },
    )

    def publish_from_url(**kwargs):
        captured.update(kwargs)
        return "publish-1"

    monkeypatch.setattr(publisher, "_publish_from_url", publish_from_url)

    job = PostJob(
        post_id="post-1",
        content_type="tiktok_video",
        text="Caption",
        media_items=[MediaItem("https://cdn.example/video.mp4", "video")],
    )
    target = Target(
        platform="tiktok",
        destination="creator",
        options={
            "access_token": "token",
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "post_mode": "DIRECT_POST",
        },
    )

    result = publisher.publish(job, target)

    assert result.ok is True
    assert captured["privacy_level"] == "PUBLIC_TO_EVERYONE"
