from __future__ import annotations

import argparse
import json

from autoposter_bot.config import load_settings
from autoposter_bot.infrastructure.auth_store import CANONICAL_ROLES
from autoposter_bot.infrastructure.persistence import build_persistence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autoposter-session",
        description="Operator-only workspace membership and browser session utility.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    issue = subparsers.add_parser("issue", help="Issue one revocable opaque session token")
    issue.add_argument("--workspace-id", type=int, required=True)
    issue.add_argument("--user-id", type=int, required=True)
    issue.add_argument("--days", type=int, default=30)

    members = subparsers.add_parser("members", help="List workspace members")
    members.add_argument("--workspace-id", type=int, required=True)

    role = subparsers.add_parser("set-role", help="Add a member or update their workspace role")
    role.add_argument("--workspace-id", type=int, required=True)
    role.add_argument("--user-id", type=int, required=True)
    role.add_argument("--role", choices=CANONICAL_ROLES, required=True)

    revoke = subparsers.add_parser("revoke", help="Revoke a session token")
    revoke.add_argument("token")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    persistence = build_persistence(load_settings())
    persistence.init_schema()
    try:
        if args.command == "issue":
            token, identity = persistence.auth.create_session(
                user_id=args.user_id,
                workspace_id=args.workspace_id,
                ttl_seconds=max(1, args.days) * 24 * 60 * 60,
            )
            print(
                json.dumps(
                    {
                        "token": token,
                        "session_id": identity.session_id,
                        "workspace_id": identity.workspace_id,
                        "user_id": identity.user_id,
                        "role": identity.role,
                        "expires_at": identity.expires_at.isoformat(),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        if args.command == "members":
            print(json.dumps(persistence.auth.list_members(args.workspace_id), ensure_ascii=False, indent=2, default=str))
            return
        if args.command == "set-role":
            member = persistence.auth.set_membership(args.workspace_id, args.user_id, args.role)
            print(json.dumps(member, ensure_ascii=False, indent=2, default=str))
            return
        if args.command == "revoke":
            print(json.dumps({"revoked": persistence.auth.revoke_session(args.token)}))
            return
        raise RuntimeError(f"Unknown command: {args.command}")
    finally:
        persistence.close()


if __name__ == "__main__":
    main()
