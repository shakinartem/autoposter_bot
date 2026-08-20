from __future__ import annotations

from datetime import datetime

import pytest

from autoposter_bot.application.sessions import WorkspaceSessionService
from autoposter_bot.db import Database
from autoposter_bot.infrastructure.auth_store import AuthStore
from autoposter_bot.infrastructure.content_store import SQLiteContentStore
from autoposter_bot.infrastructure.workspace_schema import init_workspace_schema


def _runtime(tmp_path, name: str):
    db_path = tmp_path / name
    Database(db_path).init_schema()
    raw = SQLiteContentStore(db_path)
    raw.init_schema()
    init_workspace_schema(raw)
    return raw


def test_same_session_token_switches_workspace_only_with_membership(tmp_path):
    raw = _runtime(tmp_path, "switch.sqlite3")
    now = datetime.now().isoformat()
    with raw.connect() as db:
        db.execute(
            "INSERT INTO users(id, telegram_user_id, username, full_name, is_active, created_at) VALUES (1, 101, 'owner', 'Owner', 1, ?)",
            (now,),
        )
        db.execute(
            "INSERT INTO users(id, telegram_user_id, username, full_name, is_active, created_at) VALUES (2, 202, 'member', 'Member', 1, ?)",
            (now,),
        )
        db.execute("INSERT INTO workspaces(id, name, owner_user_id, created_at) VALUES (5, 'Alpha', 1, ?)", (now,))
        db.execute("INSERT INTO workspaces(id, name, owner_user_id, created_at) VALUES (6, 'Beta', 1, ?)", (now,))
        db.execute("INSERT INTO workspaces(id, name, owner_user_id, created_at) VALUES (7, 'Forbidden', 1, ?)", (now,))

    auth = AuthStore(backend="sqlite", connect=raw.connect)
    auth.init_schema()
    auth.set_membership(5, 2, "viewer")
    auth.set_membership(6, 2, "editor")
    token, issued = auth.create_session(user_id=2, workspace_id=5, ttl_seconds=3600)
    service = WorkspaceSessionService(auth)

    before = auth.resolve_session(token)
    assert before is not None
    assert before.workspace_id == 5
    assert before.role == "viewer"

    switched = service.switch_workspace(session_id=issued.session_id, user_id=2, workspace_id=6)
    assert switched["workspace_id"] == 6
    assert switched["role"] == "editor"

    after = auth.resolve_session(token)
    assert after is not None
    assert after.session_id == issued.session_id
    assert after.workspace_id == 6
    assert after.role == "editor"

    with pytest.raises(ValueError, match="not an active member"):
        service.switch_workspace(session_id=issued.session_id, user_id=2, workspace_id=7)

    unchanged = auth.resolve_session(token)
    assert unchanged is not None
    assert unchanged.workspace_id == 6


def test_workspace_list_is_user_scoped(tmp_path):
    raw = _runtime(tmp_path, "list.sqlite3")
    now = datetime.now().isoformat()
    with raw.connect() as db:
        db.execute("INSERT INTO users(id, telegram_user_id, username, full_name, is_active, created_at) VALUES (1, 101, 'owner', 'Owner', 1, ?)", (now,))
        db.execute("INSERT INTO users(id, telegram_user_id, username, full_name, is_active, created_at) VALUES (2, 202, 'member', 'Member', 1, ?)", (now,))
        db.execute("INSERT INTO workspaces(id, name, owner_user_id, created_at) VALUES (5, 'Alpha', 1, ?)", (now,))
        db.execute("INSERT INTO workspaces(id, name, owner_user_id, created_at) VALUES (6, 'Beta', 1, ?)", (now,))
        db.execute("INSERT INTO workspaces(id, name, owner_user_id, created_at) VALUES (7, 'Hidden', 1, ?)", (now,))

    auth = AuthStore(backend="sqlite", connect=raw.connect)
    auth.init_schema()
    auth.set_membership(5, 2, "viewer")
    auth.set_membership(6, 2, "editor")
    workspaces = WorkspaceSessionService(auth).list_workspaces(2)

    assert [item["id"] for item in workspaces] == [6, 5]
    assert {item["name"] for item in workspaces} == {"Alpha", "Beta"}
    assert "Hidden" not in {item["name"] for item in workspaces}


def test_logout_revokes_same_backend_session_token(tmp_path):
    raw = _runtime(tmp_path, "logout.sqlite3")
    now = datetime.now().isoformat()
    with raw.connect() as db:
        db.execute("INSERT INTO users(id, telegram_user_id, username, full_name, is_active, created_at) VALUES (1, 101, 'owner', 'Owner', 1, ?)", (now,))
        db.execute("INSERT INTO users(id, telegram_user_id, username, full_name, is_active, created_at) VALUES (2, 202, 'member', 'Member', 1, ?)", (now,))
        db.execute("INSERT INTO workspaces(id, name, owner_user_id, created_at) VALUES (5, 'Alpha', 1, ?)", (now,))

    auth = AuthStore(backend="sqlite", connect=raw.connect)
    auth.init_schema()
    auth.set_membership(5, 2, "editor")
    token, issued = auth.create_session(user_id=2, workspace_id=5, ttl_seconds=3600)
    service = WorkspaceSessionService(auth)

    assert auth.resolve_session(token) is not None
    assert service.revoke_current(session_id=issued.session_id, user_id=2) is True
    assert auth.resolve_session(token) is None
    assert service.revoke_current(session_id=issued.session_id, user_id=2) is False
