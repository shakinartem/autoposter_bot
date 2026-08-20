from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from cryptography.fernet import Fernet

from autoposter_bot.db import Database
from autoposter_bot.infrastructure.auth_store import AuthStore
from autoposter_bot.infrastructure.content_store import SQLiteContentStore
from autoposter_bot.infrastructure.identity_store import IdentityStore
from autoposter_bot.infrastructure.login_grant_store import LoginGrantStore
from autoposter_bot.infrastructure.workspace_schema import init_workspace_schema
from autoposter_bot.integrations.telegram_oidc import TelegramOIDCProvider


def _stores(tmp_path):
    db_path = tmp_path / "login.sqlite3"
    Database(db_path).init_schema()
    raw = SQLiteContentStore(db_path)
    raw.init_schema()
    init_workspace_schema(raw)
    auth = AuthStore(backend="sqlite", connect=raw.connect)
    identities = IdentityStore(backend="sqlite", connect=raw.connect)
    grants = LoginGrantStore(backend="sqlite", connect=raw.connect, ttl_seconds=120)
    auth.init_schema()
    identities.init_schema()
    grants.init_schema()
    return raw, auth, identities, grants


def test_new_telegram_identity_creates_user_owner_workspace_and_session(tmp_path):
    _, auth, identities, grants = _stores(tmp_path)
    user = identities.find_or_create_telegram_user(
        {
            "iss": "https://oauth.telegram.org",
            "aud": "123",
            "sub": "telegram-subject-42",
            "id": 424242,
            "name": "Artem Test",
            "preferred_username": "artem_test",
        }
    )
    workspace = identities.ensure_personal_workspace(int(user["id"]), str(user["full_name"]))

    membership = auth.get_membership(int(workspace["id"]), int(user["id"]))
    assert membership is not None
    assert membership["role"] == "owner"

    grant_token = grants.issue(
        user_id=int(user["id"]),
        workspace_id=int(workspace["id"]),
        return_path="/calendar",
    )
    consumed = grants.consume(grant_token)
    assert consumed is not None
    assert consumed.return_path == "/calendar"
    assert grants.consume(grant_token) is None

    session_token, session = auth.create_session(
        user_id=consumed.user_id,
        workspace_id=consumed.workspace_id,
        ttl_seconds=3600,
    )
    resolved = auth.resolve_session(session_token)
    assert resolved is not None
    assert resolved.session_id == session.session_id
    assert resolved.role == "owner"


def test_existing_telegram_user_is_linked_instead_of_duplicated(tmp_path):
    raw, _, identities, _ = _stores(tmp_path)
    now = datetime.now().isoformat()
    with raw.connect() as db:
        db.execute(
            "INSERT INTO users(id, telegram_user_id, username, full_name, is_registered, created_at) VALUES (?, ?, ?, ?, 1, ?)",
            (77, 777777, "legacy", "Legacy User", now),
        )

    user = identities.find_or_create_telegram_user(
        {
            "sub": "new-oidc-subject-for-legacy",
            "id": 777777,
            "name": "Updated User",
            "preferred_username": "updated",
        }
    )

    assert int(user["id"]) == 77
    with raw.connect() as db:
        count = db.execute("SELECT COUNT(*) AS count FROM users WHERE telegram_user_id = ?", (777777,)).fetchone()["count"]
        identity = db.execute(
            "SELECT user_id FROM user_identities WHERE provider = 'telegram' AND subject = ?",
            ("new-oidc-subject-for-legacy",),
        ).fetchone()
    assert count == 1
    assert int(identity["user_id"]) == 77


def test_login_grant_is_hash_only_and_one_time(tmp_path):
    raw, auth, identities, grants = _stores(tmp_path)
    user = identities.find_or_create_telegram_user({"sub": "subject", "id": 909090, "name": "Grant User"})
    workspace = identities.ensure_personal_workspace(int(user["id"]), "Grant User")
    grant = grants.issue(user_id=int(user["id"]), workspace_id=int(workspace["id"]), return_path="//evil.test")

    with raw.connect() as db:
        row = db.execute("SELECT token_hash, return_path FROM login_grants").fetchone()
    assert row["token_hash"] == grants.hash_token(grant)
    assert grant not in row["token_hash"]
    assert row["return_path"] == "/"

    consumed = grants.consume(grant)
    assert consumed is not None
    assert consumed.return_path == "/"
    assert grants.consume(grant) is None
    # Ensure the grant is not itself accepted as a browser session.
    assert auth.resolve_session(grant) is None


def test_telegram_oidc_authorization_uses_pkce_encrypted_state_and_nonce(monkeypatch):
    state_key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("AUTOPOSTER_LOGIN_STATE_KEYS", state_key)
    settings = SimpleNamespace(
        telegram_oidc_client_id="123456",
        telegram_oidc_client_secret="client-secret",
        telegram_oidc_redirect_uri="https://api.example.com/auth/telegram/callback",
        telegram_oidc_scope="openid profile",
    )
    provider = TelegramOIDCProvider(settings)

    url = provider.authorization_url(return_path="/calendar")
    query = parse_qs(urlparse(url).query)

    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert "openid" in query["scope"][0]
    assert query["nonce"][0]
    assert query["state"][0]
    state = provider.state_codec.verify(query["state"][0])
    assert state.return_path == "/calendar"
    assert state.nonce == query["nonce"][0]
    assert len(state.code_verifier) >= 43


def test_telegram_oidc_exchange_sends_pkce_verifier_and_checks_nonce(monkeypatch):
    state_key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("AUTOPOSTER_LOGIN_STATE_KEYS", state_key)
    settings = SimpleNamespace(
        telegram_oidc_client_id="123456",
        telegram_oidc_client_secret="client-secret",
        telegram_oidc_redirect_uri="https://api.example.com/auth/telegram/callback",
        telegram_oidc_scope="openid profile",
    )
    provider = TelegramOIDCProvider(settings)
    auth_url = provider.authorization_url(return_path="/")
    state_token = parse_qs(urlparse(auth_url).query)["state"][0]
    decoded_state = provider.state_codec.verify(state_token)
    captured = {}

    class Response:
        ok = True
        def json(self):
            return {"id_token": "signed-id-token"}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr("autoposter_bot.integrations.telegram_oidc.requests.post", fake_post)
    monkeypatch.setattr(
        provider,
        "_verify_id_token",
        lambda token: {
            "iss": provider.ISSUER,
            "aud": provider.client_id,
            "sub": "subject-1",
            "id": 123,
            "nonce": decoded_state.nonce,
        },
    )

    claims, return_path = provider.exchange_and_verify(code="authorization-code", state_token=state_token)

    assert return_path == "/"
    assert claims["sub"] == "subject-1"
    assert captured["auth"] == ("123456", "client-secret")
    assert captured["data"]["code_verifier"] == decoded_state.code_verifier
