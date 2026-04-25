from __future__ import annotations

import argparse
import json
from datetime import datetime

from autoposter_bot.config import load_settings
from autoposter_bot.admin_bot import TelegramAdminBot
from autoposter_bot.db import Database
from autoposter_bot.loader import load_job
from autoposter_bot.models import MediaItem
from autoposter_bot.scheduler import process_due_db_jobs
from autoposter_bot.service import AutoposterService
from autoposter_bot.token_health import check_vk_token_status, send_vk_token_warning_if_needed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Autoposter bot CLI")
    parser.add_argument("--env-file", help="Path to .env file", default=None)

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Create SQLite schema")

    account_parser = subparsers.add_parser("add-account", help="Add target account/channel/profile")
    account_parser.add_argument("--name", required=True)
    account_parser.add_argument("--platform", required=True, choices=["telegram", "vk", "instagram", "tiktok"])
    account_parser.add_argument("--destination", required=True, help="Channel/group/profile identifier")
    account_parser.add_argument("--options-json", default="{}", help="Extra account options as JSON")

    subparsers.add_parser("list-accounts", help="List configured accounts")
    delete_parser = subparsers.add_parser("delete-account", help="Delete account from database by id")
    delete_parser.add_argument("account_id", type=int)

    subparsers.add_parser("oauth-connections", help="List synced OAuth connections in the local database")
    sync_oauth_parser = subparsers.add_parser(
        "sync-oauth-connections",
        help="Fetch OAuth connections from spgutils and store them locally",
    )
    sync_oauth_parser.add_argument("--telegram-user-id", type=int, default=None)

    schedule_parser = subparsers.add_parser("schedule-post", help="Create scheduled job in database")
    schedule_parser.add_argument("--post-id", required=True)
    schedule_parser.add_argument("--content-type", required=True)
    schedule_parser.add_argument("--text", required=True)
    schedule_parser.add_argument("--schedule-at", default=None, help="ISO datetime")
    schedule_parser.add_argument(
        "--media",
        action="append",
        default=[],
        help="Media in format source|media_type where source is local path or public URL",
    )
    schedule_parser.add_argument("--account-id", action="append", default=[], type=int)
    schedule_parser.add_argument("--metadata-json", default="{}")

    publish_parser = subparsers.add_parser("publish-file", help="Publish one job from a JSON file")
    publish_parser.add_argument("job_file")
    publish_parser.add_argument("--dry-run", action="store_true")

    queue_parser = subparsers.add_parser("run-due", help="Publish all due jobs from database")
    queue_parser.add_argument("--dry-run", action="store_true")

    watch_parser = subparsers.add_parser("watch", help="Continuously publish due jobs from database")
    watch_parser.add_argument("--interval", type=int, default=60)
    watch_parser.add_argument("--dry-run", action="store_true")

    subparsers.add_parser("admin-bot", help="Run Telegram admin bot for account management and test posts")

    token_parser = subparsers.add_parser("check-tokens", help="Check token health and optionally send warnings")
    token_parser.add_argument("--warning-hours", type=int, default=6)
    token_parser.add_argument("--notify", action="store_true")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    settings = load_settings(args.env_file)
    service = AutoposterService(settings)
    db = Database(settings.database_path)

    if args.command == "init-db":
        db.init_schema()
        print(f"Database initialized: {settings.database_path}")
        return

    if args.command == "add-account":
        db.init_schema()
        account_id = db.add_account(
            name=args.name,
            platform=args.platform,
            destination=args.destination,
            options=json.loads(args.options_json),
        )
        print(f"Created account #{account_id}: {args.name}")
        return

    if args.command == "list-accounts":
        db.init_schema()
        for row in db.list_accounts():
            print(f"[{row['id']}] {row['platform']} | {row['name']} | {row['destination']} | {row['options_json']}")
        return

    if args.command == "delete-account":
        db.init_schema()
        row = db.get_account(args.account_id)
        if not row:
            print(f"Account #{args.account_id} not found")
            return
        deleted = db.delete_account(args.account_id)
        if deleted:
            print(f"Deleted account #{args.account_id}: {row['platform']} | {row['name']} | {row['destination']}")
        else:
            print(f"Failed to delete account #{args.account_id}")
        return

    if args.command == "oauth-connections":
        db.init_schema()
        rows = db.list_oauth_connections()
        for row in rows:
            print(
                f"[{row['id']}] {row['platform']} | {row['account_name'] or '-'} | "
                f"{row['destination'] or '-'} | {row['status']} | key={row['connection_key']}"
            )
        return

    if args.command == "sync-oauth-connections":
        db.init_schema()
        owner_user_id = None
        if args.telegram_user_id is not None:
            user = db.get_user_by_telegram_id(args.telegram_user_id)
            owner_user_id = int(user["id"]) if user else None
            if owner_user_id is None:
                print(f"Telegram user {args.telegram_user_id} not found")
                return
        if owner_user_id is None:
            raise ValueError("--telegram-user-id is required to sync OAuth connections from spgutils")
        synced = service.sync_oauth_connections_for_user(owner_user_id)
        print(f"Synced {synced} OAuth connection(s)")
        return

    if args.command == "schedule-post":
        db.init_schema()
        media_items = [_parse_media_arg(item, index) for index, item in enumerate(args.media)]
        scheduled_at = datetime.fromisoformat(args.schedule_at) if args.schedule_at else None
        job_id = db.create_job(
            post_id=args.post_id,
            content_type=args.content_type,
            text=args.text,
            scheduled_at=scheduled_at,
            media_items=media_items,
            account_ids=args.account_id,
            metadata=json.loads(args.metadata_json),
        )
        print(f"Scheduled job #{job_id}: {args.post_id}")
        return

    if args.command == "publish-file":
        job = load_job(args.job_file)
        _print_results(service.publish_job(job, dry_run=args.dry_run))
        return

    if args.command == "run-due":
        db.init_schema()
        processed = process_due_db_jobs(service, db, dry_run=args.dry_run)
        print(f"Processed {len(processed)} due job(s): {', '.join(processed) if processed else 'none'}")
        return

    if args.command == "watch":
        from autoposter_bot.scheduler import run_polling_loop

        db.init_schema()
        print(f"Watching database queue: {settings.database_path}")
        run_polling_loop(service, db, args.interval, dry_run=args.dry_run)
        return

    if args.command == "admin-bot":
        db.init_schema()
        TelegramAdminBot(settings).run()
        return

    if args.command == "check-tokens":
        status = (
            send_vk_token_warning_if_needed(settings, warning_hours=args.warning_hours)
            if args.notify
            else check_vk_token_status(settings, warning_hours=args.warning_hours)
        )
        prefix = "OK" if status.ok else "WARN"
        print(f"[{prefix}] {status.provider}: {status.detail}")


def _parse_media_arg(value: str, order_index: int) -> MediaItem:
    raw_source, _, media_type = value.partition("|")
    return MediaItem(
        source=raw_source,
        media_type=media_type or "auto",
        order_index=order_index,
    )


def _print_results(results) -> None:
    for result in results:
        status = "OK" if result.ok else "FAIL"
        print(f"[{status}] {result.platform} -> {result.destination}: {result.detail}")


if __name__ == "__main__":
    main()
