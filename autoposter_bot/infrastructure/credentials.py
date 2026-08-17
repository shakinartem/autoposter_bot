from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from psycopg.types.json import Jsonb


SECRET_NAMES = {
    "token",
    "access_token",
    "refresh_token",
    "bot_token",
    "api_key",
    "api_secret",
    "client_secret",
    "password",
    "secret",
}
SECRET_SUFFIXES = ("_token", "_secret", "_password", "_api_key", "_api_secret")


def is_secret_field(name: str) -> bool:
    normalized = name.strip().lower()
    return normalized in SECRET_NAMES or normalized.endswith(SECRET_SUFFIXES)


def split_account_options(options: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    public: dict[str, Any] = {}
    secrets: dict[str, Any] = {}
    for key, value in options.items():
        (secrets if is_secret_field(key) else public)[key] = value
    return public, secrets


@dataclass(slots=True)
class CredentialCipher:
    fernet: MultiFernet

    @classmethod
    def from_env(cls, *, required: bool = False) -> "CredentialCipher | None":
        raw = os.getenv("AUTOPOSTER_CREDENTIAL_KEYS", "").strip()
        keys = [item.strip() for item in raw.split(",") if item.strip()]
        if not keys:
            if required:
                raise RuntimeError(
                    "AUTOPOSTER_CREDENTIAL_KEYS is required for encrypted production credentials"
                )
            return None
        try:
            fernets = [Fernet(key.encode("ascii")) for key in keys]
        except Exception as exc:
            raise RuntimeError("AUTOPOSTER_CREDENTIAL_KEYS contains an invalid Fernet key") from exc
        return cls(MultiFernet(fernets))

    def encrypt(self, payload: dict[str, Any]) -> str:
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return self.fernet.encrypt(serialized).decode("ascii")

    def decrypt(self, token: str) -> dict[str, Any]:
        try:
            raw = self.fernet.decrypt(token.encode("ascii"))
        except InvalidToken as exc:
            raise RuntimeError("Unable to decrypt social account credentials with configured keys") from exc
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("Decrypted credential payload is not an object")
        return payload

    def rotate(self, token: str) -> str:
        try:
            return self.fernet.rotate(token.encode("ascii")).decode("ascii")
        except InvalidToken as exc:
            raise RuntimeError("Unable to rotate credential payload with configured keys") from exc


class SecureContentStore:
    """Store decorator that keeps account secrets encrypted at rest."""

    def __init__(self, base: Any, *, backend: str, cipher: CredentialCipher | None) -> None:
        self.base = base
        self.backend = backend
        self.cipher = cipher

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base, name)

    def scoped(self, workspace_id: int) -> "SecureContentStore":
        scoped_factory = getattr(self.base, "scoped", None)
        if not callable(scoped_factory):
            raise AttributeError("Wrapped store does not support scoped()")
        return SecureContentStore(
            scoped_factory(workspace_id),
            backend=self.backend,
            cipher=self.cipher,
        )

    def init_schema(self) -> None:
        self.base.init_schema()
        self._init_credential_schema()

    def list_accounts(self) -> list[dict[str, Any]]:
        return [self._hydrate_account(item) for item in self.base.list_accounts()]

    def get_account(self, account_id: int) -> dict[str, Any] | None:
        item = self.base.get_account(account_id)
        return self._hydrate_account(item) if item else None

    def create_account(
        self,
        *,
        name: str,
        platform: str,
        destination: str | None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        workspace = self._workspace_context()
        if workspace is None:
            raise PermissionError("Workspace context is required to create an account")
        options = dict(options or {})
        public, secrets = split_account_options(options)
        if secrets and self.cipher is None:
            raise RuntimeError(
                "AUTOPOSTER_CREDENTIAL_KEYS must be configured before saving social credentials"
            )
        now = datetime.now()
        owner_user_id = int(workspace["owner_user_id"])
        workspace_id = int(workspace["id"])
        root = self._root_store()

        with root.connect() as connection:
            if self.backend == "postgres":
                row = connection.execute(
                    """INSERT INTO accounts(owner_user_id, name, platform, destination, options_json, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                    (
                        owner_user_id,
                        name.strip(),
                        platform.strip().lower(),
                        destination,
                        Jsonb(public),
                        now,
                    ),
                ).fetchone()
                account_id = int(row["id"])
                connection.execute(
                    """INSERT INTO workspace_accounts(workspace_id, account_id, created_at)
                    VALUES (%s, %s, %s) ON CONFLICT(workspace_id, account_id) DO NOTHING""",
                    (workspace_id, account_id, now),
                )
            else:
                cursor = connection.execute(
                    """INSERT INTO accounts(owner_user_id, name, platform, destination, options_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        owner_user_id,
                        name.strip(),
                        platform.strip().lower(),
                        destination,
                        json.dumps(public, ensure_ascii=False),
                        now.isoformat(),
                    ),
                )
                account_id = int(cursor.lastrowid)
                connection.execute(
                    """INSERT INTO workspace_accounts(workspace_id, account_id, created_at)
                    VALUES (?, ?, ?) ON CONFLICT(workspace_id, account_id) DO NOTHING""",
                    (workspace_id, account_id, now.isoformat()),
                )

        encrypted = self.cipher.encrypt(secrets) if secrets and self.cipher else ""
        self._write_account_storage(account_id, public, encrypted)
        account = self.get_account(account_id)
        if account is None:
            raise RuntimeError("Created social account could not be loaded")
        return account

    def update_account(
        self,
        account_id: int,
        *,
        name: str | None = None,
        destination: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        existing = self.get_account(account_id)
        if existing is None:
            return None
        merged_options = dict(existing.get("options") or {})
        if options is not None:
            merged_options.update(options)
        public, secrets = split_account_options(merged_options)
        if secrets and self.cipher is None:
            raise RuntimeError(
                "AUTOPOSTER_CREDENTIAL_KEYS must be configured before saving social credentials"
            )
        root = self._root_store()
        with root.connect() as connection:
            if self.backend == "postgres":
                connection.execute(
                    "UPDATE accounts SET name = %s, destination = %s WHERE id = %s",
                    (name.strip() if name is not None else existing["name"], destination if destination is not None else existing["destination"], account_id),
                )
            else:
                connection.execute(
                    "UPDATE accounts SET name = ?, destination = ? WHERE id = ?",
                    (name.strip() if name is not None else existing["name"], destination if destination is not None else existing["destination"], account_id),
                )
        encrypted = self.cipher.encrypt(secrets) if secrets and self.cipher else ""
        self._write_account_storage(account_id, public, encrypted)
        return self.get_account(account_id)

    def secure_account(self, account_id: int) -> bool:
        if self.cipher is None:
            raise RuntimeError("Credential encryption key is not configured")
        account = self.base.get_account(account_id)
        if account is None:
            return False
        public, secrets = split_account_options(dict(account.get("options") or {}))
        existing = self._read_encrypted(account_id)
        if existing:
            secrets = {**self.cipher.decrypt(existing), **secrets}
        encrypted = self.cipher.encrypt(secrets) if secrets else ""
        self._write_account_storage(account_id, public, encrypted)
        return True

    def secure_all_accounts(self) -> int:
        root = self._root_store()
        accounts = root.list_accounts()
        count = 0
        for account in accounts:
            if self.secure_account(int(account["id"])):
                count += 1
        return count

    def rotate_all_credentials(self) -> int:
        if self.cipher is None:
            raise RuntimeError("Credential encryption key is not configured")
        root = self._root_store()
        rows = self._credential_rows(root)
        count = 0
        for row in rows:
            token = row["encrypted_payload"]
            if not token:
                continue
            self._write_encrypted(int(row["account_id"]), self.cipher.rotate(token))
            count += 1
        return count

    def _workspace_context(self) -> dict[str, Any] | None:
        getter = getattr(self.base, "get_workspace", None)
        if not callable(getter):
            return None
        return getter()

    def _hydrate_account(self, account: dict[str, Any]) -> dict[str, Any]:
        result = dict(account)
        options = dict(result.get("options") or {})
        encrypted = self._read_encrypted(int(result["id"]))
        if encrypted:
            if self.cipher is None:
                raise RuntimeError(
                    "Encrypted social credentials exist but AUTOPOSTER_CREDENTIAL_KEYS is not configured"
                )
            options.update(self.cipher.decrypt(encrypted))
        result["options"] = options
        return result

    def _root_store(self) -> Any:
        current = self.base
        while hasattr(current, "base"):
            current = current.base
        return current

    def _connection(self):
        return self._root_store().connect()

    def _init_credential_schema(self) -> None:
        if self.backend == "postgres":
            sql = """
                CREATE TABLE IF NOT EXISTS account_credentials (
                    account_id BIGINT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
                    encrypted_payload TEXT NOT NULL,
                    key_version TEXT NOT NULL DEFAULT 'fernet-v1',
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """
        else:
            sql = """
                CREATE TABLE IF NOT EXISTS account_credentials (
                    account_id INTEGER PRIMARY KEY,
                    encrypted_payload TEXT NOT NULL,
                    key_version TEXT NOT NULL DEFAULT 'fernet-v1',
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                )
            """
        with self._connection() as connection:
            connection.execute(sql)

    def _read_encrypted(self, account_id: int) -> str | None:
        placeholder = "%s" if self.backend == "postgres" else "?"
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT encrypted_payload FROM account_credentials WHERE account_id = {placeholder}",
                (account_id,),
            ).fetchone()
        return row["encrypted_payload"] if row else None

    def _write_account_storage(self, account_id: int, public: dict[str, Any], encrypted: str) -> None:
        if self.backend == "postgres":
            with self._connection() as connection:
                connection.execute(
                    "UPDATE accounts SET options_json = %s WHERE id = %s",
                    (Jsonb(public), account_id),
                )
                connection.execute(
                    """INSERT INTO account_credentials(account_id, encrypted_payload, key_version, updated_at)
                    VALUES (%s, %s, 'fernet-v1', %s)
                    ON CONFLICT(account_id) DO UPDATE SET
                      encrypted_payload = EXCLUDED.encrypted_payload,
                      key_version = EXCLUDED.key_version,
                      updated_at = EXCLUDED.updated_at""",
                    (account_id, encrypted, datetime.now()),
                )
            return
        with self._connection() as connection:
            connection.execute(
                "UPDATE accounts SET options_json = ? WHERE id = ?",
                (json.dumps(public, ensure_ascii=False), account_id),
            )
            connection.execute(
                """INSERT INTO account_credentials(account_id, encrypted_payload, key_version, updated_at)
                VALUES (?, ?, 'fernet-v1', ?)
                ON CONFLICT(account_id) DO UPDATE SET
                  encrypted_payload = excluded.encrypted_payload,
                  key_version = excluded.key_version,
                  updated_at = excluded.updated_at""",
                (account_id, encrypted, datetime.now().isoformat()),
            )

    def _write_encrypted(self, account_id: int, encrypted: str) -> None:
        placeholder = "%s" if self.backend == "postgres" else "?"
        now = datetime.now() if self.backend == "postgres" else datetime.now().isoformat()
        with self._connection() as connection:
            connection.execute(
                f"UPDATE account_credentials SET encrypted_payload = {placeholder}, updated_at = {placeholder} WHERE account_id = {placeholder}",
                (encrypted, now, account_id),
            )

    def _credential_rows(self, root: Any) -> list[Any]:
        with root.connect() as connection:
            return connection.execute(
                "SELECT account_id, encrypted_payload FROM account_credentials ORDER BY account_id"
            ).fetchall()
