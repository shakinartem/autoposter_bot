from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from autoposter_bot.config import load_settings
from autoposter_bot.infrastructure.credentials import split_account_options
from autoposter_bot.infrastructure.persistence import PersistenceRuntime, build_persistence


def _runtime() -> tuple[Any, PersistenceRuntime]:
    settings = load_settings()
    runtime = build_persistence(settings)
    runtime.init_schema()
    return settings, runtime


def _create(args: argparse.Namespace) -> None:
    _, runtime = _runtime()
    try:
        root = runtime.store.base
        with root.connect() as db:
            if runtime.backend == "postgres":
                owner = db.execute("SELECT id FROM users WHERE id = %s", (args.owner_user_id,)).fetchone()
                if owner is None:
                    raise SystemExit(f"User {args.owner_user_id} does not exist in PostgreSQL")
                workspace_id = int(
                    db.execute(
                        "INSERT INTO workspaces(name, owner_user_id, created_at) VALUES (%s, %s, %s) RETURNING id",
                        (args.name, args.owner_user_id, datetime.now()),
                    ).fetchone()["id"]
                )
            else:
                owner = db.execute("SELECT id FROM users WHERE id = ?", (args.owner_user_id,)).fetchone()
                if owner is None:
                    raise SystemExit(f"Legacy user {args.owner_user_id} does not exist")
                cursor = db.execute(
                    "INSERT INTO workspaces(name, owner_user_id, created_at) VALUES (?, ?, ?)",
                    (args.name, args.owner_user_id, datetime.now().isoformat()),
                )
                workspace_id = int(cursor.lastrowid)
        print(f"workspace_id={workspace_id}")
        print("Map a long API key to this workspace id in AUTOPOSTER_API_KEYS_JSON.")
    finally:
        runtime.close()


def _list(_: argparse.Namespace) -> None:
    _, runtime = _runtime()
    try:
        root = runtime.store.base
        with root.connect() as db:
            rows = db.execute(
                "SELECT id, name, owner_user_id, created_at FROM workspaces ORDER BY id"
            ).fetchall()
        if not rows:
            print("No workspaces")
            return
        for row in rows:
            print(f"{row['id']}\t{row['name']}\towner_user_id={row['owner_user_id']}\t{row['created_at']}")
    finally:
        runtime.close()


def _link_account(args: argparse.Namespace) -> None:
    _, runtime = _runtime()
    try:
        root = runtime.store.base
        with root.connect() as db:
            if runtime.backend == "postgres":
                workspace = db.execute("SELECT id FROM workspaces WHERE id = %s", (args.workspace_id,)).fetchone()
                account = db.execute("SELECT id FROM accounts WHERE id = %s", (args.account_id,)).fetchone()
                if workspace is None:
                    raise SystemExit(f"Workspace {args.workspace_id} does not exist")
                if account is None:
                    raise SystemExit(f"Account {args.account_id} does not exist")
                db.execute(
                    """INSERT INTO workspace_accounts(workspace_id, account_id, created_at)
                    VALUES (%s, %s, %s) ON CONFLICT(workspace_id, account_id) DO NOTHING""",
                    (args.workspace_id, args.account_id, datetime.now()),
                )
            else:
                workspace = db.execute("SELECT id FROM workspaces WHERE id = ?", (args.workspace_id,)).fetchone()
                account = db.execute("SELECT id FROM accounts WHERE id = ?", (args.account_id,)).fetchone()
                if workspace is None:
                    raise SystemExit(f"Workspace {args.workspace_id} does not exist")
                if account is None:
                    raise SystemExit(f"Account {args.account_id} does not exist")
                db.execute(
                    """INSERT INTO workspace_accounts(workspace_id, account_id, created_at)
                    VALUES (?, ?, ?) ON CONFLICT(workspace_id, account_id) DO NOTHING""",
                    (args.workspace_id, args.account_id, datetime.now().isoformat()),
                )
        print(f"linked account {args.account_id} -> workspace {args.workspace_id}")
    finally:
        runtime.close()


def _encrypt_credentials(_: argparse.Namespace) -> None:
    _, runtime = _runtime()
    try:
        if runtime.store.cipher is None:
            raise SystemExit("Set AUTOPOSTER_CREDENTIAL_KEYS before encrypting credentials")
        count = runtime.store.secure_all_accounts()
        print(f"encrypted_accounts={count}")
    finally:
        runtime.close()


def _rotate_credentials(_: argparse.Namespace) -> None:
    _, runtime = _runtime()
    try:
        if runtime.store.cipher is None:
            raise SystemExit("Set AUTOPOSTER_CREDENTIAL_KEYS before rotating credentials")
        count = runtime.store.rotate_all_credentials()
        print(f"rotated_accounts={count}")
    finally:
        runtime.close()


def _import_legacy(args: argparse.Namespace) -> None:
    settings, runtime = _runtime()
    try:
        if runtime.backend != "postgres":
            raise SystemExit("import-legacy requires AUTOPOSTER_DATABASE_URL=postgresql://...")
        if runtime.store.cipher is None:
            raise SystemExit("AUTOPOSTER_CREDENTIAL_KEYS is required for legacy import")

        source_path = Path(args.sqlite_path) if args.sqlite_path else settings.database_path
        if not source_path.exists():
            raise SystemExit(f"SQLite source does not exist: {source_path}")

        source = sqlite3.connect(source_path)
        source.row_factory = sqlite3.Row
        try:
            users = source.execute("SELECT * FROM users ORDER BY id").fetchall()
            accounts = source.execute("SELECT * FROM accounts ORDER BY id").fetchall()
            has_workspaces = _sqlite_table_exists(source, "workspaces")
            workspaces = source.execute("SELECT * FROM workspaces ORDER BY id").fetchall() if has_workspaces else []
            has_links = _sqlite_table_exists(source, "workspace_accounts")
            links = source.execute("SELECT * FROM workspace_accounts").fetchall() if has_links else []

            root = runtime.store.base
            with root.connect() as target:
                for row in users:
                    target.execute(
                        """INSERT INTO users(
                            id, telegram_user_id, username, full_name, phone_number, role,
                            is_registered, registered_at, is_active, created_at
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT(id) DO UPDATE SET
                          telegram_user_id=EXCLUDED.telegram_user_id,
                          username=EXCLUDED.username,
                          full_name=EXCLUDED.full_name,
                          phone_number=EXCLUDED.phone_number,
                          role=EXCLUDED.role,
                          is_registered=EXCLUDED.is_registered,
                          registered_at=EXCLUDED.registered_at,
                          is_active=EXCLUDED.is_active""",
                        (
                            row["id"], row["telegram_user_id"], row["username"], row["full_name"],
                            row["phone_number"] if "phone_number" in row.keys() else None,
                            row["role"], bool(row["is_registered"]) if "is_registered" in row.keys() else True,
                            _parse_dt(row["registered_at"] if "registered_at" in row.keys() else None),
                            bool(row["is_active"]), _parse_dt(row["created_at"]) or datetime.now(),
                        ),
                    )

                for row in accounts:
                    options = json.loads(row["options_json"] or "{}")
                    public, secrets = split_account_options(options)
                    encrypted = runtime.store.cipher.encrypt(secrets) if secrets else ""
                    target.execute(
                        """INSERT INTO accounts(id, owner_user_id, name, platform, destination, options_json, created_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT(id) DO UPDATE SET
                          owner_user_id=EXCLUDED.owner_user_id,
                          name=EXCLUDED.name,
                          platform=EXCLUDED.platform,
                          destination=EXCLUDED.destination,
                          options_json=EXCLUDED.options_json""",
                        (
                            row["id"], row["owner_user_id"], row["name"], row["platform"],
                            row["destination"], Jsonb(public), _parse_dt(row["created_at"]) or datetime.now(),
                        ),
                    )
                    target.execute(
                        """INSERT INTO account_credentials(account_id, encrypted_payload, key_version, updated_at)
                        VALUES (%s,%s,'fernet-v1',%s)
                        ON CONFLICT(account_id) DO UPDATE SET
                          encrypted_payload=EXCLUDED.encrypted_payload,
                          key_version=EXCLUDED.key_version,
                          updated_at=EXCLUDED.updated_at""",
                        (row["id"], encrypted, datetime.now()),
                    )

                for row in workspaces:
                    target.execute(
                        """INSERT INTO workspaces(id, name, owner_user_id, created_at)
                        VALUES (%s,%s,%s,%s)
                        ON CONFLICT(id) DO UPDATE SET name=EXCLUDED.name, owner_user_id=EXCLUDED.owner_user_id""",
                        (row["id"], row["name"], row["owner_user_id"], _parse_dt(row["created_at"]) or datetime.now()),
                    )
                for row in links:
                    target.execute(
                        """INSERT INTO workspace_accounts(workspace_id, account_id, created_at)
                        VALUES (%s,%s,%s) ON CONFLICT(workspace_id, account_id) DO NOTHING""",
                        (row["workspace_id"], row["account_id"], _parse_dt(row["created_at"]) or datetime.now()),
                    )

                _reset_sequence(target, "accounts", "id")
                _reset_sequence(target, "workspaces", "id")

            print(f"imported_users={len(users)}")
            print(f"imported_accounts={len(accounts)}")
            print(f"imported_workspaces={len(workspaces)}")
            print("Account secrets were encrypted before being written to PostgreSQL.")
        finally:
            source.close()
    finally:
        runtime.close()


def _sqlite_table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _reset_sequence(connection: Any, table: str, column: str) -> None:
    connection.execute(
        f"""SELECT setval(
          pg_get_serial_sequence('{table}', '{column}'),
          COALESCE((SELECT MAX({column}) FROM {table}), 1),
          EXISTS(SELECT 1 FROM {table})
        )"""
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage Autoposter Content OS workspaces")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="Create a workspace")
    create.add_argument("--name", required=True)
    create.add_argument("--owner-user-id", required=True, type=int)
    create.set_defaults(handler=_create)

    listing = commands.add_parser("list", help="List workspaces")
    listing.set_defaults(handler=_list)

    link = commands.add_parser("link-account", help="Share a social account into a workspace")
    link.add_argument("--workspace-id", required=True, type=int)
    link.add_argument("--account-id", required=True, type=int)
    link.set_defaults(handler=_link_account)

    encrypt = commands.add_parser("encrypt-credentials", help="Move plaintext account secrets into encrypted storage")
    encrypt.set_defaults(handler=_encrypt_credentials)

    rotate = commands.add_parser("rotate-credentials", help="Re-encrypt stored credentials using the first configured key")
    rotate.set_defaults(handler=_rotate_credentials)

    legacy = commands.add_parser("import-legacy", help="Import legacy SQLite users/accounts/workspaces into PostgreSQL")
    legacy.add_argument("--sqlite-path")
    legacy.set_defaults(handler=_import_legacy)

    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
