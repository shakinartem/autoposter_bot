from __future__ import annotations

import json

from cryptography.fernet import Fernet

from autoposter_bot.db import Database
from autoposter_bot.infrastructure.content_store import SQLiteContentStore
from autoposter_bot.infrastructure.credentials import CredentialCipher, SecureContentStore, split_account_options


def test_split_account_options_keeps_public_ids_outside_secret_payload():
    public, secrets = split_account_options({
        "ig_user_id": "123",
        "graph_api_version": "v22.0",
        "access_token": "super-secret-token",
        "client_secret": "client-secret",
    })
    assert public == {"ig_user_id": "123", "graph_api_version": "v22.0"}
    assert secrets == {"access_token": "super-secret-token", "client_secret": "client-secret"}


def test_sqlite_account_secrets_are_removed_from_plaintext_storage(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("AUTOPOSTER_CREDENTIAL_KEYS", key)
    db_path = tmp_path / "autoposter.sqlite3"
    Database(db_path).init_schema()
    raw = SQLiteContentStore(db_path)
    with raw.connect() as db:
        now = "2026-08-17T20:00:00"
        db.execute("INSERT INTO users(id, telegram_user_id, username, full_name, created_at) VALUES (1, 101, 'owner', 'Owner', ?)", (now,))
        db.execute(
            "INSERT INTO accounts(id, owner_user_id, name, platform, destination, options_json, created_at) VALUES (7, 1, 'Instagram', 'instagram', 'ig', ?, ?)",
            (json.dumps({"ig_user_id": "123", "access_token": "super-secret-token", "graph_api_version": "v22.0"}), now),
        )

    secure = SecureContentStore(raw, backend="sqlite", cipher=CredentialCipher.from_env(required=True))
    secure.init_schema()
    assert secure.secure_account(7) is True

    with raw.connect() as db:
        account_row = db.execute("SELECT options_json FROM accounts WHERE id = 7").fetchone()
        credential_row = db.execute("SELECT encrypted_payload FROM account_credentials WHERE account_id = 7").fetchone()

    assert "super-secret-token" not in account_row["options_json"]
    assert json.loads(account_row["options_json"]) == {"ig_user_id": "123", "graph_api_version": "v22.0"}
    assert credential_row is not None
    assert "super-secret-token" not in credential_row["encrypted_payload"]
    hydrated = secure.get_account(7)
    assert hydrated is not None
    assert hydrated["options"]["access_token"] == "super-secret-token"


def test_multifernet_rotation_keeps_credentials_readable(tmp_path, monkeypatch):
    old_key = Fernet.generate_key().decode("ascii")
    new_key = Fernet.generate_key().decode("ascii")
    db_path = tmp_path / "autoposter.sqlite3"
    Database(db_path).init_schema()
    raw = SQLiteContentStore(db_path)
    with raw.connect() as db:
        now = "2026-08-17T20:00:00"
        db.execute("INSERT INTO users(id, telegram_user_id, username, full_name, created_at) VALUES (1, 101, 'owner', 'Owner', ?)", (now,))
        db.execute(
            "INSERT INTO accounts(id, owner_user_id, name, platform, destination, options_json, created_at) VALUES (7, 1, 'TG', 'telegram', 'chat', ?, ?)",
            (json.dumps({"access_token": "token-1"}), now),
        )

    monkeypatch.setenv("AUTOPOSTER_CREDENTIAL_KEYS", old_key)
    old_store = SecureContentStore(raw, backend="sqlite", cipher=CredentialCipher.from_env(required=True))
    old_store.init_schema()
    old_store.secure_account(7)

    monkeypatch.setenv("AUTOPOSTER_CREDENTIAL_KEYS", f"{new_key},{old_key}")
    rotating_store = SecureContentStore(raw, backend="sqlite", cipher=CredentialCipher.from_env(required=True))
    assert rotating_store.rotate_all_credentials() == 1
    assert rotating_store.get_account(7)["options"]["access_token"] == "token-1"

    monkeypatch.setenv("AUTOPOSTER_CREDENTIAL_KEYS", new_key)
    new_store = SecureContentStore(raw, backend="sqlite", cipher=CredentialCipher.from_env(required=True))
    assert new_store.get_account(7)["options"]["access_token"] == "token-1"
