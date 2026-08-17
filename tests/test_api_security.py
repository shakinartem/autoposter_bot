from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from autoposter_bot.apps.api.authorization import require_minimum_role
from autoposter_bot.apps.api.security import (
    AuthContext,
    configure_session_resolver,
    get_auth_context,
)
from autoposter_bot.infrastructure.auth_store import SessionIdentity
from datetime import datetime, timedelta, timezone


def teardown_function():
    configure_session_resolver(None)


def test_service_api_key_resolves_with_service_role(monkeypatch):
    monkeypatch.setenv(
        "AUTOPOSTER_API_KEYS_JSON",
        json.dumps({"0123456789abcdef0123456789abcdef": 7}),
    )
    monkeypatch.setenv("AUTOPOSTER_REQUIRE_API_AUTH", "1")

    auth = get_auth_context("Bearer 0123456789abcdef0123456789abcdef")

    assert auth.workspace_id == 7
    assert auth.mode == "api_key"
    assert auth.role == "service"
    assert auth.user_id is None


def test_session_resolver_produces_live_workspace_identity(monkeypatch):
    monkeypatch.delenv("AUTOPOSTER_API_KEYS_JSON", raising=False)
    monkeypatch.setenv("AUTOPOSTER_REQUIRE_API_AUTH", "1")
    identity = SessionIdentity(
        session_id="session-1",
        user_id=22,
        workspace_id=9,
        role="editor",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    configure_session_resolver(lambda token: identity if token == "aps_valid-session-token-value-1234567890" else None)

    auth = get_auth_context("Bearer aps_valid-session-token-value-1234567890")

    assert auth.workspace_id == 9
    assert auth.user_id == 22
    assert auth.role == "editor"
    assert auth.mode == "session"
    assert auth.session_id == "session-1"


def test_invalid_session_is_rejected(monkeypatch):
    monkeypatch.delenv("AUTOPOSTER_API_KEYS_JSON", raising=False)
    monkeypatch.setenv("AUTOPOSTER_REQUIRE_API_AUTH", "1")
    configure_session_resolver(lambda _: None)

    with pytest.raises(HTTPException) as exc:
        get_auth_context("Bearer aps_invalid-session-token-value-123456789")

    assert exc.value.status_code == 401


def test_role_matrix_allows_reads_but_blocks_viewer_mutations():
    viewer = AuthContext(
        workspace_id=1,
        credential_fingerprint="viewer",
        mode="session",
        user_id=2,
        role="viewer",
    )

    with pytest.raises(HTTPException) as exc:
        require_minimum_role(viewer.role, "editor")
    assert exc.value.status_code == 403

    require_minimum_role("editor", "editor")
    require_minimum_role("admin", "editor")
    require_minimum_role("owner", "admin")
    require_minimum_role("service", "owner")


def test_admin_cannot_satisfy_owner_only_boundary():
    with pytest.raises(HTTPException) as exc:
        require_minimum_role("admin", "owner")
    assert exc.value.status_code == 403
