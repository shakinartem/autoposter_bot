from __future__ import annotations

import argparse
from datetime import datetime

from autoposter_bot.config import load_settings
from autoposter_bot.infrastructure.content_store import SQLiteContentStore
from autoposter_bot.infrastructure.workspace_schema import init_workspace_schema


def _store() -> SQLiteContentStore:
    settings = load_settings()
    store = SQLiteContentStore(settings.database_path)
    store.init_schema()
    init_workspace_schema(store)
    return store


def _create(args: argparse.Namespace) -> None:
    store = _store()
    with store.connect() as db:
        owner = db.execute("SELECT id FROM users WHERE id = ?", (args.owner_user_id,)).fetchone()
        if owner is None:
            raise SystemExit(f"Legacy user {args.owner_user_id} does not exist")
        cursor = db.execute(
            "INSERT INTO workspaces(name, owner_user_id, created_at) VALUES (?, ?, ?)",
            (args.name, args.owner_user_id, datetime.now().isoformat()),
        )
        workspace_id = int(cursor.lastrowid)
    print(f"workspace_id={workspace_id}")
    print("Add a long random key mapped to this id in AUTOPOSTER_API_KEYS_JSON.")


def _list(_: argparse.Namespace) -> None:
    store = _store()
    with store.connect() as db:
        rows = db.execute(
            "SELECT id, name, owner_user_id, created_at FROM workspaces ORDER BY id"
        ).fetchall()
    if not rows:
        print("No workspaces")
        return
    for row in rows:
        print(f"{row['id']}\t{row['name']}\towner_user_id={row['owner_user_id']}\t{row['created_at']}")


def _link_account(args: argparse.Namespace) -> None:
    store = _store()
    with store.connect() as db:
        workspace = db.execute("SELECT id FROM workspaces WHERE id = ?", (args.workspace_id,)).fetchone()
        account = db.execute("SELECT id FROM accounts WHERE id = ?", (args.account_id,)).fetchone()
        if workspace is None:
            raise SystemExit(f"Workspace {args.workspace_id} does not exist")
        if account is None:
            raise SystemExit(f"Account {args.account_id} does not exist")
        db.execute(
            """
            INSERT INTO workspace_accounts(workspace_id, account_id, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(workspace_id, account_id) DO NOTHING
            """,
            (args.workspace_id, args.account_id, datetime.now().isoformat()),
        )
    print(f"linked account {args.account_id} -> workspace {args.workspace_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage Autoposter Content OS workspaces")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="Create a workspace for an existing bot user")
    create.add_argument("--name", required=True)
    create.add_argument("--owner-user-id", required=True, type=int)
    create.set_defaults(handler=_create)

    listing = commands.add_parser("list", help="List workspaces")
    listing.set_defaults(handler=_list)

    link = commands.add_parser("link-account", help="Share a social account into a workspace")
    link.add_argument("--workspace-id", required=True, type=int)
    link.add_argument("--account-id", required=True, type=int)
    link.set_defaults(handler=_link_account)

    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
