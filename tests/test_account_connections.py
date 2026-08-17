from __future__ import annotations

from datetime import datetime

from cryptography.fernet import Fernet

from autoposter_bot.db import Database
from autoposter_bot.infrastructure.content_store import SQLiteContentStore
from autoposter_bot.infrastructure.credentials import CredentialCipher, SecureContentStore
from autoposter_bot.infrastructure.workspace_schema import init_workspace_schema
from autoposter_bot.infrastructure.workspace_store import WorkspaceContentStore


def test_workspace_account_creation_encrypts_secret_options(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("AUTOPOSTER_CREDENTIAL_KEYS", key)
    db_path = tmp_path / "autoposter.sqlite3"
    Database(db_path).init_schema()
    raw = SQLiteContentStore(db_path)
    init_workspace_schema(raw)
    now = datetime.now().isoformat()
    with raw.connect() as db:
        db.execute(
            "INSERT INTO users(id, telegram_user_id, username, full_name, created_at) VALUES (1, 101, 'owner', 'Owner', ?)",
            (now,),
        )
        db.execute(
            "INSERT INTO workspaces(id, name, owner_user_id, created_at) VALUES (5, 'Workspace', 1, ?)",
            (now,),
        )

    scoped = SecureContentStore(
        WorkspaceContentStore(raw, 5),
        backend="sqlite",
        cipher=CredentialCipher.from_env(required=True),
    )
    scoped._init_credential_schema()
    account = scoped.create_account(
        name="Instagram main",
        platform="instagram",
        destination="@brand",
        options={
            "ig_user_id": "12345",
            "api_flow": "instagram_login",
            "access_token": "EA-secret-token",
        },
    )

    assert account["options"]["access_token"] == "EA-secret-token"
    assert account["options"]["ig_user_id"] == "12345"

    with raw.connect() as db:
        row = db.execute(
            "SELECT options_json FROM accounts WHERE id = ?", (account["id"],)
        ).fetchone()
        encrypted = db.execute(
            "SELECT encrypted_payload FROM account_credentials WHERE account_id = ?",
            (account["id"],),
        ).fetchone()

    assert "EA-secret-token" not in row["options_json"]
    assert "12345" in row["options_json"]
    assert encrypted is not None
    assert "EA-secret-token" not in encrypted["encrypted_payload"]
