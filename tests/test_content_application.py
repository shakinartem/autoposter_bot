from autoposter_bot.application.content import ContentApplication
from autoposter_bot.infrastructure.content_store import SQLiteContentStore


def test_master_updates_only_synced_variants(tmp_path):
    store = SQLiteContentStore(tmp_path / "autoposter.sqlite3")
    store.init_schema()
    app = ContentApplication(store)

    master = app.create(title="Launch", body="Master v1")
    telegram = app.upsert_variant(master.id, "telegram")

    assert telegram is not None
    assert telegram.sync_with_master is True
    assert telegram.text == "Master v1"

    app.update(master.id, body="Master v2")
    telegram = store.get_variant_for_platform(master.id, "telegram")
    assert telegram is not None
    assert telegram.text == "Master v2"

    telegram = app.upsert_variant(master.id, "telegram", text="Telegram native copy")
    assert telegram is not None
    assert telegram.sync_with_master is False

    app.update(master.id, body="Master v3")
    telegram = store.get_variant_for_platform(master.id, "telegram")
    assert telegram is not None
    assert telegram.text == "Telegram native copy"


def test_resync_restores_master_content(tmp_path):
    store = SQLiteContentStore(tmp_path / "autoposter.sqlite3")
    store.init_schema()
    app = ContentApplication(store)

    master = app.create(title="Title", body="Master")
    app.upsert_variant(master.id, "vk", text="VK override")

    variant = app.upsert_variant(master.id, "vk", sync_with_master=True)

    assert variant is not None
    assert variant.sync_with_master is True
    assert variant.title == "Title"
    assert variant.text == "Master"
