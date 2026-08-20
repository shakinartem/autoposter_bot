from __future__ import annotations

from datetime import datetime

import pytest

from autoposter_bot.db import Database
from autoposter_bot.infrastructure.auth_store import AuthStore
from autoposter_bot.infrastructure.content_store import SQLiteContentStore
from autoposter_bot.infrastructure.invitation_store import InvitationStore
from autoposter_bot.infrastructure.workspace_schema import init_workspace_schema


def _stores(tmp_path):
    db_path = tmp_path / "invitations.sqlite3"
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
            "INSERT INTO users(id, telegram_user_id, username, full_name, is_active, created_at) VALUES (2, 202, 'member', 'Member', 1, ?)",
            (now,),
        )
        db.execute(
            "INSERT INTO workspaces(id, name, owner_user_id, created_at) VALUES (5, 'Team Alpha', 1, ?)",
            (now,),
        )
    auth = AuthStore(backend="sqlite", connect=raw.connect)
    invites = InvitationStore(backend="sqlite", connect=raw.connect)
    auth.init_schema()
    invites.init_schema()
    return raw, auth, invites


def test_invite_token_is_hash_only_previewable_and_single_use(tmp_path):
    raw, auth, invites = _stores(tmp_path)
    token, issued = invites.issue(
        workspace_id=5,
        role="editor",
        created_by_user_id=1,
        ttl_seconds=3600,
    )

    assert token.startswith("awi_")
    preview = invites.preview(token)
    assert preview is not None
    assert preview.workspace_id == 5
    assert preview.workspace_name == "Team Alpha"
    assert preview.role == "editor"

    with raw.connect() as db:
        row = db.execute(
            "SELECT token_hash FROM workspace_invitations WHERE id = ?",
            (issued.id,),
        ).fetchone()
    assert row is not None
    assert row["token_hash"] == invites.hash_token(token)
    assert token not in row["token_hash"]

    accepted = invites.accept(token, user_id=2)
    assert accepted.consumed is True
    membership = auth.get_membership(5, 2)
    assert membership is not None and membership["role"] == "editor"
    assert invites.preview(token) is None

    with pytest.raises(ValueError, match="already used|invalid"):
        invites.accept(token, user_id=2)


def test_invite_never_downgrades_existing_stronger_role(tmp_path):
    _, auth, invites = _stores(tmp_path)
    auth.set_membership(5, 2, "admin")
    token, _ = invites.issue(
        workspace_id=5,
        role="viewer",
        created_by_user_id=1,
        ttl_seconds=3600,
    )

    accepted = invites.accept(token, user_id=2)

    assert accepted.role == "admin"
    membership = auth.get_membership(5, 2)
    assert membership is not None and membership["role"] == "admin"


def test_revoked_invite_cannot_be_previewed_or_accepted(tmp_path):
    _, _, invites = _stores(tmp_path)
    token, issued = invites.issue(
        workspace_id=5,
        role="viewer",
        created_by_user_id=1,
        ttl_seconds=3600,
    )

    assert invites.revoke(issued.id, workspace_id=5) is True
    assert invites.preview(token) is None
    assert invites.revoke(issued.id, workspace_id=5) is False

    with pytest.raises(ValueError, match="revoked|invalid"):
        invites.accept(token, user_id=2)


def test_expired_invite_is_terminal(tmp_path):
    _, _, invites = _stores(tmp_path)
    token, issued = invites.issue(
        workspace_id=5,
        role="editor",
        created_by_user_id=1,
        ttl_seconds=300,
    )
    with invites.connect() as db:
        db.execute(
            "UPDATE workspace_invitations SET expires_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", issued.id),
        )

    assert invites.preview(token) is None
    with pytest.raises(ValueError, match="expired|invalid"):
        invites.accept(token, user_id=2)


def test_active_invite_listing_does_not_return_raw_token(tmp_path):
    _, _, invites = _stores(tmp_path)
    token, issued = invites.issue(
        workspace_id=5,
        role="editor",
        created_by_user_id=1,
        ttl_seconds=3600,
    )

    rows = invites.list_workspace(5)

    assert len(rows) == 1
    assert rows[0]["id"] == issued.id
    assert "token_hash" not in rows[0]
    assert token not in str(rows[0])
