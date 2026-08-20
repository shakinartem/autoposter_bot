from datetime import datetime

from autoposter_bot.application.content import ContentApplication
from autoposter_bot.apps.api.security import get_auth_context
from autoposter_bot.domain.content import Publication
from autoposter_bot.infrastructure.content_store import SQLiteContentStore
from autoposter_bot.infrastructure.workspace_schema import init_workspace_schema
from autoposter_bot.infrastructure.workspace_store import WorkspaceContentStore


def _seed_tenants(store: SQLiteContentStore) -> None:
    now = datetime.now().isoformat()
    with store.connect() as db:
        for user_id, telegram_id in ((101, 900101), (202, 900202)):
            db.execute(
                "INSERT INTO users(id, telegram_user_id, username, full_name, role, is_registered, credit_balance, is_active, created_at) VALUES (?, ?, ?, ?, 'user', 1, 0, 1, ?)",
                (user_id, telegram_id, f"user{user_id}", f"User {user_id}", now),
            )
        db.execute("INSERT INTO workspaces(id, name, owner_user_id, created_at) VALUES (1, 'One', 101, ?)", (now,))
        db.execute("INSERT INTO workspaces(id, name, owner_user_id, created_at) VALUES (2, 'Two', 202, ?)", (now,))
        db.execute("INSERT INTO accounts(id, owner_user_id, name, platform, destination, options_json, created_at) VALUES (11, 101, 'One TG', 'telegram', '-1001', '{}', ?)", (now,))
        db.execute("INSERT INTO accounts(id, owner_user_id, name, platform, destination, options_json, created_at) VALUES (22, 202, 'Two TG', 'telegram', '-1002', '{}', ?)", (now,))


def test_workspace_cannot_read_another_workspace_objects(tmp_path):
    base = SQLiteContentStore(tmp_path / "autoposter.sqlite3")
    base.init_schema()
    init_workspace_schema(base)
    _seed_tenants(base)

    one = WorkspaceContentStore(base, 1)
    two = WorkspaceContentStore(base, 2)
    content = ContentApplication(one).create(title="Secret", body="Workspace one")
    variant = ContentApplication(one).upsert_variant(content.id, "telegram")
    assert variant is not None
    publication = Publication(variant_id=variant.id, platform="telegram", account_id=11)
    one.save_publication(publication)

    assert one.get_content(content.id) is not None
    assert two.get_content(content.id) is None
    assert two.get_variant(variant.id) is None
    assert two.get_publication(publication.id) is None
    assert one.get_account(11) is not None
    assert two.get_account(11) is None
    assert two.get_account(22) is not None


def test_api_key_is_bound_to_workspace(monkeypatch):
    key_one = "workspace-one-super-secret"
    key_two = "workspace-two-super-secret"
    monkeypatch.setenv("AUTOPOSTER_API_KEYS_JSON", f'{{"{key_one}": 1, "{key_two}": 2}}')
    monkeypatch.setenv("AUTOPOSTER_REQUIRE_API_AUTH", "1")

    auth_one = get_auth_context(f"Bearer {key_one}")
    auth_two = get_auth_context(f"Bearer {key_two}")

    assert auth_one.workspace_id == 1
    assert auth_two.workspace_id == 2
    assert auth_one.credential_fingerprint != auth_two.credential_fingerprint
