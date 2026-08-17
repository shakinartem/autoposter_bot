from __future__ import annotations

from datetime import datetime

from autoposter_bot.db import Database
from autoposter_bot.infrastructure.auth_store import AuthStore
from autoposter_bot.infrastructure.content_store import SQLiteContentStore
from autoposter_bot.infrastructure.workspace_schema import init_workspace_schema


def _auth_store(tmp_path):
    db_path = tmp_path / "auth.sqlite3"
    Database(db_path).init_schema()
    raw = SQLiteContentStore(db_path)
    raw.init_schema()
    init_workspace_schema(raw)
    now = datetime.now().isoformat()
    with raw.connect() as db:
        db.execute(
            "INSERT INTO users(id, telegram_user_id, username, full_name, is_active, created_at) VALUES (1, 101, 'owner', 'Owner', 1, ?)",
            (now,),
        )
        db.execute(
            "INSERT INTO users(id, telegram_user_id, username, full_name, is_active, created_at) VALUES (2, 202, 'editor', 'Editor', 1, ?)",
            (now,),
        )
        db.execute(
            "INSERT INTO workspaces(id, name, owner_user_id, created_at) VALUES (5, 'Workspace', 1, ?)",
            (now,),
        )
    auth = AuthStore(backend="sqlite", connect=raw.connect)
    auth.init_schema()
    return raw, auth


def test_owner_membership_is_bootstrapped_from_workspace_owner(tmp_path):
    _, auth = _auth_store(tmp_path)

    membership = auth.get_membership(5, 1)

    assert membership is not None
    assert membership["role"] == "owner"


def test_session_token_is_hashed_at_rest_and_revocable(tmp_path):
    raw, auth = _auth_store(tmp_path)
    token, issued = auth.create_session(user_id=1, workspace_id=5, ttl_seconds=3600)

    resolved = auth.resolve_session(token)
    assert resolved is not None
    assert resolved.session_id == issued.session_id
    assert resolved.user_id == 1
    assert resolved.workspace_id == 5
    assert resolved.role == "owner"

    with raw.connect() as db:
        row = db.execute(
            "SELECT token_hash FROM user_sessions WHERE id = ?",
            (issued.session_id,),
        ).fetchone()
    assert row is not None
    assert token not in row["token_hash"]
    assert row["token_hash"] == auth.hash_token(token)

    assert auth.revoke_session(token) is True
    assert auth.resolve_session(token) is None


def test_role_change_is_visible_to_existing_session_without_reissue(tmp_path):
    _, auth = _auth_store(tmp_path)
    auth.set_membership(5, 2, "viewer")
    token, _ = auth.create_session(user_id=2, workspace_id=5, ttl_seconds=3600)

    before = auth.resolve_session(token)
    assert before is not None and before.role == "viewer"

    auth.set_membership(5, 2, "editor")
    after = auth.resolve_session(token)
    assert after is not None and after.role == "editor"


def test_removing_member_revokes_their_workspace_sessions(tmp_path):
    _, auth = _auth_store(tmp_path)
    auth.set_membership(5, 2, "editor")
    token, _ = auth.create_session(user_id=2, workspace_id=5, ttl_seconds=3600)

    assert auth.resolve_session(token) is not None
    assert auth.remove_membership(5, 2) is True
    assert auth.resolve_session(token) is None
