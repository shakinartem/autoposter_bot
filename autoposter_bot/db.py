from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from autoposter_bot.models import MediaItem, PostJob, Target


SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    platform TEXT NOT NULL,
    destination TEXT,
    options_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_post_id TEXT NOT NULL UNIQUE,
    content_type TEXT NOT NULL,
    text TEXT NOT NULL,
    scheduled_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
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

    def add_account(
        self,
        name: str,
        platform: str,
        destination: str | None,
        options: dict,
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO accounts(name, platform, destination, options_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    name,
                    platform.lower(),
                    destination,
                    json.dumps(options, ensure_ascii=False),
                    datetime.utcnow().isoformat(),
                ),
            )
            return int(cursor.lastrowid)

    def list_accounts(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            cursor = connection.execute(
                "SELECT id, name, platform, destination, options_json, created_at FROM accounts ORDER BY platform, name"
            )
            return list(cursor.fetchall())

    def get_account(self, account_id: int) -> sqlite3.Row | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id, name, platform, destination, options_json, created_at FROM accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
            return row

    def delete_account(self, account_id: int) -> bool:
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            return cursor.rowcount > 0

    def update_account(
        self,
        account_id: int,
        *,
        name: str | None = None,
        destination: str | None = None,
        options: dict | None = None,
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
            cursor = connection.execute(
                f"UPDATE accounts SET {', '.join(updates)} WHERE id = ?",
                values,
            )
            return cursor.rowcount > 0

    def replace_account_options_for_platform(self, platform: str, options_factory) -> int:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, options_json FROM accounts WHERE platform = ?",
                (platform.lower(),),
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
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO jobs(external_post_id, content_type, text, scheduled_at, status, metadata_json, created_at)
                VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    post_id,
                    content_type,
                    text,
                    scheduled_at.isoformat() if scheduled_at else None,
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
