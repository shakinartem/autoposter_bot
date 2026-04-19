from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from autoposter_bot.models import MediaItem, PostJob, Target


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL UNIQUE,
    username TEXT,
    full_name TEXT,
    role TEXT NOT NULL DEFAULT 'user',
    credit_balance INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    price_rub INTEGER NOT NULL DEFAULT 0,
    monthly_credit_grant INTEGER NOT NULL DEFAULT 0,
    monthly_post_limit INTEGER,
    features_json TEXT NOT NULL DEFAULT '{}',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    plan_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    started_at TEXT NOT NULL,
    expires_at TEXT,
    auto_renew INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(plan_id) REFERENCES plans(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS credit_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    delta INTEGER NOT NULL,
    reason TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_entitlements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    platform TEXT,
    quantity INTEGER NOT NULL DEFAULT 1,
    period_key TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS referral_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    code TEXT NOT NULL UNIQUE,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS referral_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_user_id INTEGER NOT NULL,
    referred_user_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'registered',
    reward_amount INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(referrer_user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(referred_user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER,
    name TEXT NOT NULL UNIQUE,
    platform TEXT NOT NULL,
    destination TEXT,
    options_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(owner_user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER,
    external_post_id TEXT NOT NULL UNIQUE,
    content_type TEXT NOT NULL,
    text TEXT NOT NULL,
    scheduled_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(owner_user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS job_media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    media_type TEXT NOT NULL,
    order_index INTEGER NOT NULL DEFAULT 0,
    options_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS job_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE,
    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
);
"""


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def init_schema(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate_job_media_table(connection)
            self._migrate_accounts_table(connection)
            self._migrate_jobs_table(connection)
            self._seed_default_plans(connection)

    def _migrate_job_media_table(self, connection: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(job_media)").fetchall()
        }
        if "source" not in columns and "path" in columns:
            connection.execute("ALTER TABLE job_media ADD COLUMN source TEXT")
            connection.execute("UPDATE job_media SET source = path WHERE source IS NULL")
        if "options_json" not in columns:
            connection.execute("ALTER TABLE job_media ADD COLUMN options_json TEXT NOT NULL DEFAULT '{}'")

    def _migrate_accounts_table(self, connection: sqlite3.Connection) -> None:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(accounts)").fetchall()}
        if "owner_user_id" not in columns:
            connection.execute("ALTER TABLE accounts ADD COLUMN owner_user_id INTEGER")

    def _migrate_jobs_table(self, connection: sqlite3.Connection) -> None:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()}
        if "owner_user_id" not in columns:
            connection.execute("ALTER TABLE jobs ADD COLUMN owner_user_id INTEGER")

    def _seed_default_plans(self, connection: sqlite3.Connection) -> None:
        defaults = [
            (
                "trial",
                "Trial",
                0,
                0,
                2,
                {
                    "support": "basic",
                    "accounts_per_platform": 1,
                    "extra_account_price_rub": 350,
                    "extra_post_price_rub": 35,
                },
            ),
            (
                "start",
                "Start",
                500,
                0,
                4,
                {
                    "support": "standard",
                    "accounts_per_platform": 1,
                    "extra_account_price_rub": 350,
                    "extra_post_price_rub": 35,
                },
            ),
            (
                "growth",
                "Growth",
                1250,
                0,
                8,
                {
                    "support": "standard",
                    "accounts_per_platform": 2,
                    "extra_account_price_rub": 350,
                    "extra_post_price_rub": 35,
                },
            ),
            (
                "business",
                "Business",
                2500,
                0,
                15,
                {
                    "support": "priority",
                    "accounts_per_platform": 4,
                    "extra_account_price_rub": 350,
                    "extra_post_price_rub": 35,
                },
            ),
            (
                "scale",
                "Scale",
                3750,
                0,
                25,
                {
                    "support": "priority",
                    "accounts_per_platform": 8,
                    "extra_account_price_rub": 350,
                    "extra_post_price_rub": 35,
                    "team": True,
                },
            ),
        ]
        for code, name, price_rub, monthly_credit_grant, monthly_post_limit, features in defaults:
            existing = connection.execute(
                "SELECT id FROM plans WHERE code = ?",
                (code,),
            ).fetchone()
            if existing:
                connection.execute(
                    """
                    UPDATE plans
                    SET name = ?, price_rub = ?, monthly_credit_grant = ?, monthly_post_limit = ?, features_json = ?, is_active = 1
                    WHERE code = ?
                    """,
                    (
                        name,
                        price_rub,
                        monthly_credit_grant,
                        monthly_post_limit,
                        json.dumps(features, ensure_ascii=False),
                        code,
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO plans(code, name, price_rub, monthly_credit_grant, monthly_post_limit, features_json, is_active, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        code,
                        name,
                        price_rub,
                        monthly_credit_grant,
                        monthly_post_limit,
                        json.dumps(features, ensure_ascii=False),
                        datetime.utcnow().isoformat(),
                    ),
                )

        connection.execute(
            "UPDATE plans SET is_active = 0 WHERE code IN ('creator', 'agency')"
        )

    def ensure_user(self, telegram_user_id: int, username: str | None, full_name: str | None) -> sqlite3.Row:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE telegram_user_id = ?",
                (telegram_user_id,),
            ).fetchone()
            if row:
                connection.execute(
                    "UPDATE users SET username = ?, full_name = ? WHERE telegram_user_id = ?",
                    (username, full_name, telegram_user_id),
                )
                refreshed = connection.execute(
                    "SELECT * FROM users WHERE telegram_user_id = ?",
                    (telegram_user_id,),
                ).fetchone()
                return refreshed
            connection.execute(
                """
                INSERT INTO users(telegram_user_id, username, full_name, role, credit_balance, is_active, created_at)
                VALUES (?, ?, ?, 'user', 0, 1, ?)
                """,
                (telegram_user_id, username, full_name, datetime.utcnow().isoformat()),
            )
            return connection.execute(
                "SELECT * FROM users WHERE telegram_user_id = ?",
                (telegram_user_id,),
            ).fetchone()

    def get_user_by_telegram_id(self, telegram_user_id: int) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM users WHERE telegram_user_id = ?",
                (telegram_user_id,),
            ).fetchone()

    def get_user(self, user_id: int) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()

    def set_user_role(self, user_id: int, role: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE users SET role = ? WHERE id = ?",
                (role, user_id),
            )

    def list_plans(self, only_active: bool = True) -> list[sqlite3.Row]:
        query = "SELECT * FROM plans"
        params: tuple = ()
        if only_active:
            query += " WHERE is_active = 1"
        query += " ORDER BY price_rub, id"
        with self.connect() as connection:
            return list(connection.execute(query, params).fetchall())

    def get_plan(self, plan_id: int) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM plans WHERE id = ?",
                (plan_id,),
            ).fetchone()

    def get_plan_by_code(self, code: str) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM plans WHERE code = ?",
                (code,),
            ).fetchone()

    def get_active_subscription(self, user_id: int) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT s.*, p.code AS plan_code, p.name AS plan_name
                FROM subscriptions s
                JOIN plans p ON p.id = s.plan_id
                WHERE s.user_id = ? AND s.status = 'active'
                ORDER BY s.created_at DESC
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()

    def add_credit_transaction(self, user_id: int, delta: int, reason: str, metadata: dict | None = None) -> int:
        with self.connect() as connection:
            connection.execute(
                "UPDATE users SET credit_balance = credit_balance + ? WHERE id = ?",
                (delta, user_id),
            )
            cursor = connection.execute(
                """
                INSERT INTO credit_ledger(user_id, delta, reason, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    delta,
                    reason,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    datetime.utcnow().isoformat(),
                ),
            )
            return int(cursor.lastrowid)

    def list_credit_ledger(self, user_id: int, limit: int = 10) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """
                    SELECT id, delta, reason, metadata_json, created_at
                    FROM credit_ledger
                    WHERE user_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (user_id, limit),
                ).fetchall()
            )

    def add_entitlement(
        self,
        user_id: int,
        *,
        kind: str,
        period_key: str,
        quantity: int = 1,
        platform: str | None = None,
        metadata: dict | None = None,
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO user_entitlements(user_id, kind, platform, quantity, period_key, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    kind,
                    platform.lower() if platform else None,
                    quantity,
                    period_key,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    datetime.utcnow().isoformat(),
                ),
            )
            return int(cursor.lastrowid)

    def get_entitlement_quantity(
        self,
        user_id: int,
        *,
        kind: str,
        period_key: str,
        platform: str | None = None,
    ) -> int:
        with self.connect() as connection:
            if platform is None:
                row = connection.execute(
                    """
                    SELECT COALESCE(SUM(quantity), 0) AS quantity
                    FROM user_entitlements
                    WHERE user_id = ? AND kind = ? AND period_key = ? AND platform IS NULL
                    """,
                    (user_id, kind, period_key),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT COALESCE(SUM(quantity), 0) AS quantity
                    FROM user_entitlements
                    WHERE user_id = ? AND kind = ? AND period_key = ? AND platform = ?
                    """,
                    (user_id, kind, period_key, platform.lower()),
                ).fetchone()
            return int(row["quantity"] if row else 0)

    def get_or_create_referral_code(self, user_id: int, preferred_code: str) -> str:
        normalized = preferred_code.lower().replace(" ", "_")
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT code FROM referral_codes WHERE user_id = ? AND is_active = 1 ORDER BY id LIMIT 1",
                (user_id,),
            ).fetchone()
            if existing:
                return str(existing["code"])

            candidate = normalized
            suffix = 1
            while connection.execute(
                "SELECT 1 FROM referral_codes WHERE code = ?",
                (candidate,),
            ).fetchone():
                suffix += 1
                candidate = f"{normalized}{suffix}"

            connection.execute(
                """
                INSERT INTO referral_codes(user_id, code, is_active, created_at)
                VALUES (?, ?, 1, ?)
                """,
                (user_id, candidate, datetime.utcnow().isoformat()),
            )
            return candidate

    def get_referral_code_owner(self, code: str) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT rc.code, rc.user_id, u.full_name, u.username
                FROM referral_codes rc
                JOIN users u ON u.id = rc.user_id
                WHERE rc.code = ? AND rc.is_active = 1
                LIMIT 1
                """,
                (code.lower(),),
            ).fetchone()

    def has_referral_event_for_referred_user(self, referred_user_id: int) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM referral_events WHERE referred_user_id = ? LIMIT 1",
                (referred_user_id,),
            ).fetchone()
            return row is not None

    def create_referral_event(
        self,
        *,
        referrer_user_id: int,
        referred_user_id: int,
        code: str,
        status: str = "registered",
        reward_amount: int = 0,
        metadata: dict | None = None,
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO referral_events(referrer_user_id, referred_user_id, code, status, reward_amount, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    referrer_user_id,
                    referred_user_id,
                    code.lower(),
                    status,
                    reward_amount,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    datetime.utcnow().isoformat(),
                ),
            )
            return int(cursor.lastrowid)

    def get_referral_summary(self, user_id: int) -> sqlite3.Row:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT
                    (SELECT code FROM referral_codes WHERE user_id = ? AND is_active = 1 ORDER BY id LIMIT 1) AS code,
                    COALESCE((SELECT COUNT(*) FROM referral_events WHERE referrer_user_id = ?), 0) AS invited_count,
                    COALESCE((SELECT SUM(reward_amount) FROM referral_events WHERE referrer_user_id = ?), 0) AS total_rewards
                """,
                (user_id, user_id, user_id),
            ).fetchone()

    def activate_subscription(
        self,
        user_id: int,
        plan_id: int,
        *,
        started_at: datetime | None = None,
        expires_at: datetime | None = None,
        auto_renew: bool = False,
    ) -> int:
        started_at = started_at or datetime.utcnow()
        with self.connect() as connection:
            connection.execute(
                "UPDATE subscriptions SET status = 'expired' WHERE user_id = ? AND status = 'active'",
                (user_id,),
            )
            cursor = connection.execute(
                """
                INSERT INTO subscriptions(user_id, plan_id, status, started_at, expires_at, auto_renew, created_at)
                VALUES (?, ?, 'active', ?, ?, ?, ?)
                """,
                (
                    user_id,
                    plan_id,
                    started_at.isoformat(),
                    expires_at.isoformat() if expires_at else None,
                    1 if auto_renew else 0,
                    datetime.utcnow().isoformat(),
                ),
            )
            return int(cursor.lastrowid)

    def add_account(
        self,
        name: str,
        platform: str,
        destination: str | None,
        options: dict,
        owner_user_id: int | None = None,
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO accounts(owner_user_id, name, platform, destination, options_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    owner_user_id,
                    name,
                    platform.lower(),
                    destination,
                    json.dumps(options, ensure_ascii=False),
                    datetime.utcnow().isoformat(),
                ),
            )
            return int(cursor.lastrowid)

    def count_accounts_for_user(self, user_id: int, platform: str | None = None) -> int:
        with self.connect() as connection:
            if platform is None:
                row = connection.execute(
                    "SELECT COUNT(*) AS count FROM accounts WHERE owner_user_id = ?",
                    (user_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM accounts
                    WHERE owner_user_id = ? AND platform = ?
                    """,
                    (user_id, platform.lower()),
                ).fetchone()
            return int(row["count"] if row else 0)

    def list_accounts(self, owner_user_id: int | None = None) -> list[sqlite3.Row]:
        with self.connect() as connection:
            if owner_user_id is None:
                cursor = connection.execute(
                    "SELECT id, owner_user_id, name, platform, destination, options_json, created_at FROM accounts ORDER BY platform, name"
                )
            else:
                cursor = connection.execute(
                    """
                    SELECT id, owner_user_id, name, platform, destination, options_json, created_at
                    FROM accounts
                    WHERE owner_user_id = ?
                    ORDER BY platform, name
                    """,
                    (owner_user_id,),
                )
            return list(cursor.fetchall())

    def get_account(self, account_id: int, owner_user_id: int | None = None) -> sqlite3.Row | None:
        with self.connect() as connection:
            if owner_user_id is None:
                row = connection.execute(
                    "SELECT id, owner_user_id, name, platform, destination, options_json, created_at FROM accounts WHERE id = ?",
                    (account_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT id, owner_user_id, name, platform, destination, options_json, created_at
                    FROM accounts
                    WHERE id = ? AND owner_user_id = ?
                    """,
                    (account_id, owner_user_id),
                ).fetchone()
            return row

    def delete_account(self, account_id: int, owner_user_id: int | None = None) -> bool:
        with self.connect() as connection:
            if owner_user_id is None:
                cursor = connection.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            else:
                cursor = connection.execute(
                    "DELETE FROM accounts WHERE id = ? AND owner_user_id = ?",
                    (account_id, owner_user_id),
                )
            return cursor.rowcount > 0

    def update_account(
        self,
        account_id: int,
        *,
        name: str | None = None,
        destination: str | None = None,
        options: dict | None = None,
        owner_user_id: int | None = None,
    ) -> bool:
        updates: list[str] = []
        values: list[object] = []
        if name is not None:
            updates.append("name = ?")
            values.append(name)
        if destination is not None:
            updates.append("destination = ?")
            values.append(destination)
        if options is not None:
            updates.append("options_json = ?")
            values.append(json.dumps(options, ensure_ascii=False))
        if not updates:
            return False
        values.append(account_id)
        with self.connect() as connection:
            if owner_user_id is None:
                cursor = connection.execute(
                    f"UPDATE accounts SET {', '.join(updates)} WHERE id = ?",
                    values,
                )
            else:
                values.append(owner_user_id)
                cursor = connection.execute(
                    f"UPDATE accounts SET {', '.join(updates)} WHERE id = ? AND owner_user_id = ?",
                    values,
                )
            return cursor.rowcount > 0

    def replace_account_options_for_platform(
        self,
        platform: str,
        options_factory,
        owner_user_id: int | None = None,
    ) -> int:
        with self.connect() as connection:
            if owner_user_id is None:
                rows = connection.execute(
                    "SELECT id, options_json FROM accounts WHERE platform = ?",
                    (platform.lower(),),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT id, options_json FROM accounts WHERE platform = ? AND owner_user_id = ?",
                    (platform.lower(), owner_user_id),
                ).fetchall()
            updated = 0
            for row in rows:
                current_options = json.loads(row["options_json"] or "{}")
                next_options = options_factory(current_options)
                if next_options == current_options:
                    continue
                connection.execute(
                    "UPDATE accounts SET options_json = ? WHERE id = ?",
                    (json.dumps(next_options, ensure_ascii=False), int(row["id"])),
                )
                updated += 1
            return updated

    def create_job(
        self,
        post_id: str,
        content_type: str,
        text: str,
        scheduled_at: datetime | None,
        media_items: list[MediaItem],
        account_ids: list[int],
        metadata: dict,
        owner_user_id: int | None = None,
        status: str = "pending",
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO jobs(owner_user_id, external_post_id, content_type, text, scheduled_at, status, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    owner_user_id,
                    post_id,
                    content_type,
                    text,
                    scheduled_at.isoformat() if scheduled_at else None,
                    status,
                    json.dumps(metadata, ensure_ascii=False),
                    datetime.utcnow().isoformat(),
                ),
            )
            job_id = int(cursor.lastrowid)
            connection.executemany(
                """
                INSERT INTO job_media(job_id, source, media_type, order_index, options_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        job_id,
                        item.source,
                        item.media_type,
                        item.order_index,
                        json.dumps(item.options, ensure_ascii=False),
                    )
                    for item in media_items
                ],
            )
            connection.executemany(
                "INSERT INTO job_targets(job_id, account_id) VALUES (?, ?)",
                [(job_id, account_id) for account_id in account_ids],
            )
            return job_id

    def count_jobs_for_user_in_period(
        self,
        user_id: int,
        *,
        started_at: datetime,
        finished_at: datetime,
    ) -> int:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM jobs
                WHERE owner_user_id = ?
                  AND created_at >= ?
                  AND created_at < ?
                """,
                (user_id, started_at.isoformat(), finished_at.isoformat()),
            ).fetchone()
            return int(row["count"] if row else 0)

    def get_due_jobs(self, now: datetime) -> list[PostJob]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, external_post_id, content_type, text, scheduled_at, metadata_json
                FROM jobs
                WHERE status = 'pending'
                  AND (scheduled_at IS NULL OR scheduled_at <= ?)
                ORDER BY scheduled_at IS NULL, scheduled_at, id
                """,
                (now.isoformat(),),
            ).fetchall()

            jobs: list[PostJob] = []
            for row in rows:
                job_id = int(row["id"])
                media_rows = connection.execute(
                    """
                    SELECT source, media_type, order_index, options_json
                    FROM job_media
                    WHERE job_id = ?
                    ORDER BY order_index, id
                    """,
                    (job_id,),
                ).fetchall()
                target_rows = connection.execute(
                    """
                    SELECT a.id, a.name, a.platform, a.destination, a.options_json
                    FROM job_targets jt
                    JOIN accounts a ON a.id = jt.account_id
                    WHERE jt.job_id = ?
                    ORDER BY a.platform, a.name
                    """,
                    (job_id,),
                ).fetchall()
                jobs.append(
                    PostJob(
                        post_id=row["external_post_id"],
                        content_type=row["content_type"],
                        text=row["text"],
                        scheduled_at=datetime.fromisoformat(row["scheduled_at"]) if row["scheduled_at"] else None,
                        media_items=[
                            MediaItem(
                                source=media_row["source"],
                                media_type=media_row["media_type"],
                                order_index=int(media_row["order_index"]),
                                options=json.loads(media_row["options_json"] or "{}"),
                            )
                            for media_row in media_rows
                        ],
                        targets=[
                            Target(
                                platform=target_row["platform"],
                                destination=target_row["destination"],
                                account_id=int(target_row["id"]),
                                account_name=target_row["name"],
                                options=json.loads(target_row["options_json"] or "{}"),
                            )
                            for target_row in target_rows
                        ],
                        metadata={"job_id": job_id, **json.loads(row["metadata_json"] or "{}")},
                    )
                )
            return jobs

    def set_job_status(self, job_id: int, status: str) -> None:
        with self.connect() as connection:
            connection.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
