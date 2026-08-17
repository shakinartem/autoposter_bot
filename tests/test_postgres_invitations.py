from __future__ import annotations

import os
from datetime import datetime

import pytest

from autoposter_bot.infrastructure.auth_store import AuthStore
from autoposter_bot.infrastructure.invitation_store import InvitationStore
from autoposter_bot.infrastructure.postgres_store import PostgresContentStore

POSTGRES_URL = os.getenv("AUTOPOSTER_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="PostgreSQL integration URL is not configured")


def test_postgres_workspace_invitation_is_hash_only_single_use_and_membership_safe():
    assert POSTGRES_URL is not None
    store = PostgresContentStore(POSTGRES_URL)
    auth = AuthStore(backend="postgres", connect=store.connect)
    invites = InvitationStore(backend="postgres", connect=store.connect)
    try:
        store.init_schema()
        auth.init_schema()
        invites.init_schema()
        now = datetime.now()
        with store.connect() as db:
            db.execute("TRUNCATE TABLE users CASCADE")
            db.execute(
                "INSERT INTO users(id, telegram_user_id, username, full_name, is_registered, created_at) VALUES (%s, %s, %s, %s, TRUE, %s)",
                (301, 900301, "invite_owner", "Invite Owner", now),
            )
            db.execute(
                "INSERT INTO users(id, telegram_user_id, username, full_name, is_registered, created_at) VALUES (%s, %s, %s, %s, TRUE, %s)",
                (302, 900302, "invite_member", "Invite Member", now),
            )
            workspace_id = int(
                db.execute(
                    "INSERT INTO workspaces(name, owner_user_id, created_at) VALUES (%s, %s, %s) RETURNING id",
                    ("Postgres Invite Team", 301, now),
                ).fetchone()["id"]
            )

        auth.init_schema()
        token, issued = invites.issue(
            workspace_id=workspace_id,
            role="editor",
            created_by_user_id=301,
            ttl_seconds=3600,
        )
        assert token.startswith("awi_")
        preview = invites.preview(token)
        assert preview is not None and preview.role == "editor"

        with store.connect() as db:
            row = db.execute(
                "SELECT token_hash FROM workspace_invitations WHERE id = %s",
                (issued.id,),
            ).fetchone()
        assert row is not None
        assert row["token_hash"] == invites.hash_token(token)
        assert token not in row["token_hash"]

        accepted = invites.accept(token, user_id=302)
        assert accepted.consumed is True
        member = auth.get_membership(workspace_id, 302)
        assert member is not None and member["role"] == "editor"
        assert invites.preview(token) is None

        with pytest.raises(ValueError):
            invites.accept(token, user_id=302)
    finally:
        store.close()


def test_postgres_invitation_revoke_is_workspace_scoped():
    assert POSTGRES_URL is not None
    store = PostgresContentStore(POSTGRES_URL)
    auth = AuthStore(backend="postgres", connect=store.connect)
    invites = InvitationStore(backend="postgres", connect=store.connect)
    try:
        store.init_schema()
        auth.init_schema()
        invites.init_schema()
        now = datetime.now()
        with store.connect() as db:
            db.execute("TRUNCATE TABLE users CASCADE")
            db.execute(
                "INSERT INTO users(id, telegram_user_id, username, full_name, is_registered, created_at) VALUES (%s, %s, %s, %s, TRUE, %s)",
                (401, 900401, "owner_a", "Owner A", now),
            )
            db.execute(
                "INSERT INTO users(id, telegram_user_id, username, full_name, is_registered, created_at) VALUES (%s, %s, %s, %s, TRUE, %s)",
                (402, 900402, "owner_b", "Owner B", now),
            )
            workspace_a = int(db.execute(
                "INSERT INTO workspaces(name, owner_user_id, created_at) VALUES (%s, %s, %s) RETURNING id",
                ("A", 401, now),
            ).fetchone()["id"])
            workspace_b = int(db.execute(
                "INSERT INTO workspaces(name, owner_user_id, created_at) VALUES (%s, %s, %s) RETURNING id",
                ("B", 402, now),
            ).fetchone()["id"])

        auth.init_schema()
        token, invitation = invites.issue(
            workspace_id=workspace_a,
            role="viewer",
            created_by_user_id=401,
            ttl_seconds=3600,
        )
        assert invites.revoke(invitation.id, workspace_id=workspace_b) is False
        assert invites.preview(token) is not None
        assert invites.revoke(invitation.id, workspace_id=workspace_a) is True
        assert invites.preview(token) is None
    finally:
        store.close()
