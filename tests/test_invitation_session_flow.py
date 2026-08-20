from __future__ import annotations

from datetime import datetime

from autoposter_bot.application.sessions import WorkspaceSessionService
from autoposter_bot.db import Database
from autoposter_bot.infrastructure.auth_store import AuthStore
from autoposter_bot.infrastructure.content_store import SQLiteContentStore
from autoposter_bot.infrastructure.invitation_store import InvitationStore
from autoposter_bot.infrastructure.workspace_schema import init_workspace_schema


def test_accepting_invite_moves_same_session_into_team_workspace(tmp_path):
    db_path = tmp_path / "invite-session.sqlite3"
    Database(db_path).init_schema()
    raw = SQLiteContentStore(db_path)
    raw.init_schema()
    init_workspace_schema(raw)
    now = datetime.now().isoformat()
    with raw.connect() as db:
        db.execute(
            "INSERT INTO users(id, telegram_user_id, username, full_name, is_active, created_at) VALUES (1, 101, 'team_owner', 'Team Owner', 1, ?)",
            (now,),
        )
        db.execute(
            "INSERT INTO users(id, telegram_user_id, username, full_name, is_active, created_at) VALUES (2, 202, 'invitee', 'Invitee', 1, ?)",
            (now,),
        )
        db.execute(
            "INSERT INTO workspaces(id, name, owner_user_id, created_at) VALUES (5, 'Team Workspace', 1, ?)",
            (now,),
        )
        db.execute(
            "INSERT INTO workspaces(id, name, owner_user_id, created_at) VALUES (6, 'Personal Workspace', 2, ?)",
            (now,),
        )

    auth = AuthStore(backend="sqlite", connect=raw.connect)
    invites = InvitationStore(backend="sqlite", connect=raw.connect)
    auth.init_schema()
    invites.init_schema()
    auth.set_membership(6, 2, "owner")
    raw_token, issued_session = auth.create_session(user_id=2, workspace_id=6, ttl_seconds=3600)
    invite_token, _ = invites.issue(
        workspace_id=5,
        role="editor",
        created_by_user_id=1,
        ttl_seconds=3600,
    )

    before = auth.resolve_session(raw_token)
    assert before is not None
    assert before.workspace_id == 6
    assert before.role == "owner"

    invitation = invites.accept(invite_token, user_id=2)
    switched = WorkspaceSessionService(auth).switch_workspace(
        session_id=issued_session.session_id,
        user_id=2,
        workspace_id=invitation.workspace_id,
    )

    after = auth.resolve_session(raw_token)
    assert switched["role"] == "editor"
    assert after is not None
    assert after.session_id == before.session_id
    assert after.workspace_id == 5
    assert after.role == "editor"
