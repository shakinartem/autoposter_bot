from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from autoposter_bot.db import Database
from autoposter_bot.domain.content import (
    ContentItem,
    ContentStatus,
    MediaAsset,
    PlatformVariant,
    Publication,
    PublicationStatus,
)
from autoposter_bot.platforms.base import PublicationResult


class SQLiteContentStore:
    """Persistence boundary for the Content OS domain.

    SQLite remains the development/runtime default so the current bot keeps
    working. API/domain code depends on this store interface rather than on
    Telegram-specific DB methods, which lets us replace this implementation
    with PostgreSQL later without changing routes or application services.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def init_schema(self) -> None:
        # Existing users/accounts are still authoritative during staged rollout.
        Database(self.db_path).init_schema()
        migration_path = Path(__file__).resolve().parents[2] / "migrations" / "002_content_os.sql"
        if not migration_path.exists():
            raise RuntimeError(f"Content OS migration not found: {migration_path}")
        with self.connect() as connection:
            connection.executescript(migration_path.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------
    # Content
    # ------------------------------------------------------------------
    def save_content(self, item: ContentItem, workspace_id: int | None = None) -> ContentItem:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO content_items (
                    id, workspace_id, title, body, cta, links_json,
                    hashtags_json, status, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    workspace_id = excluded.workspace_id,
                    title = excluded.title,
                    body = excluded.body,
                    cta = excluded.cta,
                    links_json = excluded.links_json,
                    hashtags_json = excluded.hashtags_json,
                    status = excluded.status,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    item.id,
                    workspace_id,
                    item.title,
                    item.body,
                    item.cta,
                    json.dumps(item.links, ensure_ascii=False),
                    json.dumps(item.hashtags, ensure_ascii=False),
                    item.status.value,
                    json.dumps(item.metadata, ensure_ascii=False),
                    item.created_at.isoformat(),
                    item.updated_at.isoformat(),
                ),
            )
            connection.execute("DELETE FROM content_media WHERE content_id = ?", (item.id,))
            self._save_media_links(connection, item.media, content_id=item.id, workspace_id=workspace_id)
        return item

    def get_content(self, content_id: str) -> ContentItem | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM content_items WHERE id = ?", (content_id,)
            ).fetchone()
            if row is None:
                return None
            media = self._load_media(connection, content_id=content_id)
            return self._row_to_content(row, media)

    def list_content(self, *, limit: int = 100, offset: int = 0) -> list[ContentItem]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM content_items
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
            return [
                self._row_to_content(row, self._load_media(connection, content_id=row["id"]))
                for row in rows
            ]

    # ------------------------------------------------------------------
    # Platform variants
    # ------------------------------------------------------------------
    def save_variant(self, variant: PlatformVariant) -> PlatformVariant:
        now = datetime.now().isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO platform_variants (
                    id, content_id, platform, title, text, fields_json,
                    sync_with_master, revision, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    text = excluded.text,
                    fields_json = excluded.fields_json,
                    sync_with_master = excluded.sync_with_master,
                    revision = excluded.revision,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    variant.id,
                    variant.content_id,
                    variant.platform.lower(),
                    variant.title,
                    variant.text,
                    json.dumps(variant.fields, ensure_ascii=False),
                    int(variant.sync_with_master),
                    variant.revision,
                    json.dumps(variant.metadata, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            connection.execute("DELETE FROM variant_media WHERE variant_id = ?", (variant.id,))
            self._save_media_links(connection, variant.media, variant_id=variant.id)
        return variant

    def get_variant(self, variant_id: str) -> PlatformVariant | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM platform_variants WHERE id = ?", (variant_id,)
            ).fetchone()
            if row is None:
                return None
            return self._row_to_variant(row, self._load_media(connection, variant_id=variant_id))

    def get_variant_for_platform(self, content_id: str, platform: str) -> PlatformVariant | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM platform_variants
                WHERE content_id = ? AND platform = ?
                """,
                (content_id, platform.lower()),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_variant(row, self._load_media(connection, variant_id=row["id"]))

    def list_variants(self, content_id: str) -> list[PlatformVariant]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM platform_variants
                WHERE content_id = ?
                ORDER BY platform
                """,
                (content_id,),
            ).fetchall()
            return [
                self._row_to_variant(row, self._load_media(connection, variant_id=row["id"]))
                for row in rows
            ]

    # ------------------------------------------------------------------
    # Publications
    # ------------------------------------------------------------------
    def save_publication(self, publication: Publication) -> Publication:
        now = datetime.now().isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO publications_v2 (
                    id, variant_id, platform, account_id, destination,
                    scheduled_at, status, provider_tracking_id, external_post_id, external_url,
                    published_at, attempt_count, last_error_code,
                    last_error_message, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    destination = excluded.destination,
                    scheduled_at = excluded.scheduled_at,
                    status = excluded.status,
                    provider_tracking_id = excluded.provider_tracking_id,
                    external_post_id = excluded.external_post_id,
                    external_url = excluded.external_url,
                    published_at = excluded.published_at,
                    attempt_count = excluded.attempt_count,
                    last_error_code = excluded.last_error_code,
                    last_error_message = excluded.last_error_message,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    publication.id,
                    publication.variant_id,
                    publication.platform.lower(),
                    publication.account_id,
                    publication.destination,
                    publication.scheduled_at.isoformat() if publication.scheduled_at else None,
                    publication.status.value,
                    publication.provider_tracking_id,
                    publication.external_post_id,
                    publication.external_url,
                    publication.published_at.isoformat() if publication.published_at else None,
                    publication.attempt_count,
                    publication.last_error_code,
                    publication.last_error_message,
                    json.dumps(publication.metadata, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        return publication

    def get_publication(self, publication_id: str) -> Publication | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM publications_v2 WHERE id = ?", (publication_id,)
            ).fetchone()
            return self._row_to_publication(row) if row else None

    def list_publications(
        self,
        *,
        status: PublicationStatus | None = None,
        limit: int = 100,
    ) -> list[Publication]:
        with self.connect() as connection:
            if status is None:
                rows = connection.execute(
                    """
                    SELECT * FROM publications_v2
                    ORDER BY COALESCE(scheduled_at, created_at) DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM publications_v2
                    WHERE status = ?
                    ORDER BY COALESCE(scheduled_at, created_at) DESC
                    LIMIT ?
                    """,
                    (status.value, limit),
                ).fetchall()
            return [self._row_to_publication(row) for row in rows]

    def record_attempt(self, publication: Publication, result: PublicationResult) -> None:
        if publication.attempt_count <= 0:
            return
        now = datetime.now().isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO publication_attempts (
                    publication_id, attempt_number, status,
                    external_post_id, provider_tracking_id, error_code, error_message,
                    retryable, raw_response_json, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(publication_id, attempt_number) DO UPDATE SET
                    status = excluded.status,
                    external_post_id = excluded.external_post_id,
                    provider_tracking_id = excluded.provider_tracking_id,
                    error_code = excluded.error_code,
                    error_message = excluded.error_message,
                    retryable = excluded.retryable,
                    raw_response_json = excluded.raw_response_json,
                    finished_at = excluded.finished_at
                """,
                (
                    publication.id,
                    publication.attempt_count,
                    result.status,
                    result.external_post_id,
                    result.provider_tracking_id,
                    result.error_code,
                    result.error_message,
                    int(result.retryable),
                    json.dumps(result.raw_response, ensure_ascii=False),
                    now,
                    now,
                ),
            )

    # ------------------------------------------------------------------
    # Legacy account bridge
    # ------------------------------------------------------------------
    def list_accounts(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, owner_user_id, name, platform, destination,
                       options_json, created_at
                FROM accounts
                ORDER BY platform, name
                """
            ).fetchall()
            return [self._account_dict(row) for row in rows]

    def get_account(self, account_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, owner_user_id, name, platform, destination,
                       options_json, created_at
                FROM accounts WHERE id = ?
                """,
                (account_id,),
            ).fetchone()
            return self._account_dict(row) if row else None

    # ------------------------------------------------------------------
    # Mapping helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _row_to_content(row: sqlite3.Row, media: list[MediaAsset]) -> ContentItem:
        return ContentItem(
            id=row["id"],
            title=row["title"],
            body=row["body"],
            cta=row["cta"],
            links=json.loads(row["links_json"] or "[]"),
            hashtags=json.loads(row["hashtags_json"] or "[]"),
            media=media,
            status=ContentStatus(row["status"]),
            metadata=json.loads(row["metadata_json"] or "{}"),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _row_to_variant(row: sqlite3.Row, media: list[MediaAsset]) -> PlatformVariant:
        return PlatformVariant(
            id=row["id"],
            content_id=row["content_id"],
            platform=row["platform"],
            title=row["title"],
            text=row["text"],
            media=media,
            fields=json.loads(row["fields_json"] or "{}"),
            sync_with_master=bool(row["sync_with_master"]),
            revision=int(row["revision"]),
            metadata=json.loads(row["metadata_json"] or "{}"),
        )

    @staticmethod
    def _row_to_publication(row: sqlite3.Row) -> Publication:
        return Publication(
            id=row["id"],
            variant_id=row["variant_id"],
            platform=row["platform"],
            account_id=int(row["account_id"]),
            destination=row["destination"],
            scheduled_at=(datetime.fromisoformat(row["scheduled_at"]) if row["scheduled_at"] else None),
            status=PublicationStatus(row["status"]),
            provider_tracking_id=row["provider_tracking_id"],
            external_post_id=row["external_post_id"],
            external_url=row["external_url"],
            published_at=(datetime.fromisoformat(row["published_at"]) if row["published_at"] else None),
            attempt_count=int(row["attempt_count"]),
            last_error_code=row["last_error_code"],
            last_error_message=row["last_error_message"],
            metadata=json.loads(row["metadata_json"] or "{}"),
        )

    @staticmethod
    def _account_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "owner_user_id": row["owner_user_id"],
            "name": row["name"],
            "platform": row["platform"],
            "destination": row["destination"],
            "options": json.loads(row["options_json"] or "{}"),
            "created_at": row["created_at"],
        }

    def _save_media_links(
        self,
        connection: sqlite3.Connection,
        media: list[MediaAsset],
        *,
        content_id: str | None = None,
        variant_id: str | None = None,
        workspace_id: int | None = None,
    ) -> None:
        for index, asset in enumerate(media):
            connection.execute(
                """
                INSERT INTO media_assets (
                    id, workspace_id, source, media_type, alt_text,
                    metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source = excluded.source,
                    media_type = excluded.media_type,
                    alt_text = excluded.alt_text,
                    metadata_json = excluded.metadata_json
                """,
                (
                    asset.id,
                    workspace_id,
                    asset.source,
                    asset.media_type,
                    asset.alt_text,
                    json.dumps(asset.metadata, ensure_ascii=False),
                    datetime.now().isoformat(),
                ),
            )
            if content_id:
                connection.execute(
                    "INSERT INTO content_media(content_id, media_id, order_index) VALUES (?, ?, ?)",
                    (content_id, asset.id, index),
                )
            if variant_id:
                connection.execute(
                    "INSERT INTO variant_media(variant_id, media_id, order_index) VALUES (?, ?, ?)",
                    (variant_id, asset.id, index),
                )

    @staticmethod
    def _load_media(
        connection: sqlite3.Connection,
        *,
        content_id: str | None = None,
        variant_id: str | None = None,
    ) -> list[MediaAsset]:
        if content_id:
            rows = connection.execute(
                """
                SELECT m.* FROM media_assets m
                JOIN content_media cm ON cm.media_id = m.id
                WHERE cm.content_id = ?
                ORDER BY cm.order_index
                """,
                (content_id,),
            ).fetchall()
        elif variant_id:
            rows = connection.execute(
                """
                SELECT m.* FROM media_assets m
                JOIN variant_media vm ON vm.media_id = m.id
                WHERE vm.variant_id = ?
                ORDER BY vm.order_index
                """,
                (variant_id,),
            ).fetchall()
        else:
            return []
        return [
            MediaAsset(
                id=row["id"],
                source=row["source"],
                media_type=row["media_type"],
                alt_text=row["alt_text"],
                metadata=json.loads(row["metadata_json"] or "{}"),
            )
            for row in rows
        ]
