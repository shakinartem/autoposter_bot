from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from autoposter_bot.domain.content import (
    ContentItem,
    ContentStatus,
    MediaAsset,
    PlatformVariant,
    Publication,
    PublicationStatus,
)
from autoposter_bot.platforms.base import PublicationResult


class PostgresContentStore:
    """Production persistence backend for Content OS and scheduled workers."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        max_size = max(2, int(os.getenv("AUTOPOSTER_DB_POOL_SIZE", "10")))
        self.pool = ConnectionPool(
            conninfo=database_url,
            min_size=1,
            max_size=max_size,
            open=False,
            kwargs={"row_factory": dict_row},
        )
        self._opened = False

    def open(self) -> None:
        if self._opened:
            return
        self.pool.open(wait=True)
        self._opened = True

    def close(self) -> None:
        if not self._opened:
            return
        self.pool.close()
        self._opened = False

    @contextmanager
    def connect(self) -> Iterator[Any]:
        self.open()
        with self.pool.connection() as connection:
            with connection.transaction():
                yield connection

    def init_schema(self) -> None:
        migration = Path(__file__).resolve().parents[2] / "migrations" / "004_postgres_content_os.sql"
        if not migration.exists():
            raise RuntimeError(f"PostgreSQL Content OS migration not found: {migration}")
        with self.connect() as connection:
            connection.execute(migration.read_text(encoding="utf-8"))

    def scoped(self, workspace_id: int) -> "PostgresWorkspaceStore":
        return PostgresWorkspaceStore(self, workspace_id)

    # ------------------------------------------------------------------
    # Content
    # ------------------------------------------------------------------
    def save_content(self, item: ContentItem, workspace_id: int | None = None) -> ContentItem:
        if workspace_id is None:
            workspace_id = self.content_workspace_id(item.id)
        if workspace_id is None:
            raise ValueError("workspace_id is required for PostgreSQL content")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO content_items (
                    id, workspace_id, title, body, cta, links_json,
                    hashtags_json, status, metadata_json, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(id) DO UPDATE SET
                    workspace_id = EXCLUDED.workspace_id,
                    title = EXCLUDED.title,
                    body = EXCLUDED.body,
                    cta = EXCLUDED.cta,
                    links_json = EXCLUDED.links_json,
                    hashtags_json = EXCLUDED.hashtags_json,
                    status = EXCLUDED.status,
                    metadata_json = EXCLUDED.metadata_json,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    item.id,
                    workspace_id,
                    item.title,
                    item.body,
                    item.cta,
                    Jsonb(item.links),
                    Jsonb(item.hashtags),
                    item.status.value,
                    Jsonb(item.metadata),
                    item.created_at,
                    item.updated_at,
                ),
            )
            connection.execute("DELETE FROM content_media WHERE content_id = %s", (item.id,))
            self._save_media_links(connection, item.media, content_id=item.id, workspace_id=workspace_id)
        return item

    def get_content(self, content_id: str) -> ContentItem | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM content_items WHERE id = %s", (content_id,)).fetchone()
            if row is None:
                return None
            return self._row_to_content(row, self._load_media(connection, content_id=content_id))

    def list_content(self, *, limit: int = 100, offset: int = 0) -> list[ContentItem]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM content_items ORDER BY updated_at DESC LIMIT %s OFFSET %s",
                (limit, offset),
            ).fetchall()
            return [
                self._row_to_content(row, self._load_media(connection, content_id=row["id"]))
                for row in rows
            ]

    def content_workspace_id(self, content_id: str) -> int | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT workspace_id FROM content_items WHERE id = %s",
                (content_id,),
            ).fetchone()
        return int(row["workspace_id"]) if row else None

    # ------------------------------------------------------------------
    # Variants
    # ------------------------------------------------------------------
    def save_variant(self, variant: PlatformVariant) -> PlatformVariant:
        workspace_id = self.content_workspace_id(variant.content_id)
        if workspace_id is None:
            raise ValueError("Variant parent content does not exist")
        now = datetime.now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO platform_variants (
                    id, content_id, platform, title, text, fields_json,
                    sync_with_master, revision, metadata_json, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(id) DO UPDATE SET
                    title = EXCLUDED.title,
                    text = EXCLUDED.text,
                    fields_json = EXCLUDED.fields_json,
                    sync_with_master = EXCLUDED.sync_with_master,
                    revision = EXCLUDED.revision,
                    metadata_json = EXCLUDED.metadata_json,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    variant.id,
                    variant.content_id,
                    variant.platform.lower(),
                    variant.title,
                    variant.text,
                    Jsonb(variant.fields),
                    variant.sync_with_master,
                    variant.revision,
                    Jsonb(variant.metadata),
                    now,
                    now,
                ),
            )
            connection.execute("DELETE FROM variant_media WHERE variant_id = %s", (variant.id,))
            self._save_media_links(connection, variant.media, variant_id=variant.id, workspace_id=workspace_id)
        return variant

    def get_variant(self, variant_id: str) -> PlatformVariant | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM platform_variants WHERE id = %s", (variant_id,)).fetchone()
            if row is None:
                return None
            return self._row_to_variant(row, self._load_media(connection, variant_id=variant_id))

    def get_variant_for_platform(self, content_id: str, platform: str) -> PlatformVariant | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM platform_variants WHERE content_id = %s AND platform = %s",
                (content_id, platform.lower()),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_variant(row, self._load_media(connection, variant_id=row["id"]))

    def list_variants(self, content_id: str) -> list[PlatformVariant]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM platform_variants WHERE content_id = %s ORDER BY platform",
                (content_id,),
            ).fetchall()
            return [
                self._row_to_variant(row, self._load_media(connection, variant_id=row["id"]))
                for row in rows
            ]

    def variant_workspace_id(self, variant_id: str) -> int | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT c.workspace_id FROM platform_variants v
                JOIN content_items c ON c.id = v.content_id WHERE v.id = %s""",
                (variant_id,),
            ).fetchone()
        return int(row["workspace_id"]) if row else None

    # ------------------------------------------------------------------
    # Publications
    # ------------------------------------------------------------------
    def save_publication(self, publication: Publication) -> Publication:
        now = datetime.now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO publications_v2 (
                    id, variant_id, platform, account_id, destination,
                    scheduled_at, status, external_post_id, external_url,
                    published_at, attempt_count, last_error_code,
                    last_error_message, metadata_json, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(id) DO UPDATE SET
                    destination = EXCLUDED.destination,
                    scheduled_at = EXCLUDED.scheduled_at,
                    status = EXCLUDED.status,
                    external_post_id = EXCLUDED.external_post_id,
                    external_url = EXCLUDED.external_url,
                    published_at = EXCLUDED.published_at,
                    attempt_count = EXCLUDED.attempt_count,
                    last_error_code = EXCLUDED.last_error_code,
                    last_error_message = EXCLUDED.last_error_message,
                    metadata_json = EXCLUDED.metadata_json,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    publication.id,
                    publication.variant_id,
                    publication.platform.lower(),
                    publication.account_id,
                    publication.destination,
                    publication.scheduled_at,
                    publication.status.value,
                    publication.external_post_id,
                    publication.external_url,
                    publication.published_at,
                    publication.attempt_count,
                    publication.last_error_code,
                    publication.last_error_message,
                    Jsonb(publication.metadata),
                    now,
                    now,
                ),
            )
        return publication

    def get_publication(self, publication_id: str) -> Publication | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM publications_v2 WHERE id = %s", (publication_id,)).fetchone()
        return self._row_to_publication(row) if row else None

    def list_publications(self, *, status: PublicationStatus | None = None, limit: int = 100) -> list[Publication]:
        with self.connect() as connection:
            if status is None:
                rows = connection.execute(
                    "SELECT * FROM publications_v2 ORDER BY COALESCE(scheduled_at, created_at) DESC LIMIT %s",
                    (limit,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM publications_v2 WHERE status = %s ORDER BY COALESCE(scheduled_at, created_at) DESC LIMIT %s",
                    (status.value, limit),
                ).fetchall()
        return [self._row_to_publication(row) for row in rows]

    def publication_workspace_id(self, publication_id: str) -> int | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT c.workspace_id FROM publications_v2 p
                JOIN platform_variants v ON v.id = p.variant_id
                JOIN content_items c ON c.id = v.content_id WHERE p.id = %s""",
                (publication_id,),
            ).fetchone()
        return int(row["workspace_id"]) if row else None

    def record_attempt(self, publication: Publication, result: PublicationResult) -> None:
        if publication.attempt_count <= 0:
            return
        now = datetime.now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO publication_attempts (
                    publication_id, attempt_number, status,
                    external_post_id, error_code, error_message,
                    retryable, raw_response_json, started_at, finished_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(publication_id, attempt_number) DO UPDATE SET
                    status = EXCLUDED.status,
                    external_post_id = EXCLUDED.external_post_id,
                    error_code = EXCLUDED.error_code,
                    error_message = EXCLUDED.error_message,
                    retryable = EXCLUDED.retryable,
                    raw_response_json = EXCLUDED.raw_response_json,
                    finished_at = EXCLUDED.finished_at
                """,
                (
                    publication.id,
                    publication.attempt_count,
                    result.status,
                    result.external_post_id,
                    result.error_code,
                    result.error_message,
                    result.retryable,
                    Jsonb(result.raw_response),
                    now,
                    now,
                ),
            )

    # ------------------------------------------------------------------
    # Workspaces / accounts
    # ------------------------------------------------------------------
    def get_workspace(self, workspace_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id, name, owner_user_id, created_at FROM workspaces WHERE id = %s",
                (workspace_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "name": row["name"],
            "owner_user_id": int(row["owner_user_id"]),
            "created_at": self._date_text(row["created_at"]),
        }

    def list_accounts(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, owner_user_id, name, platform, destination, options_json, created_at FROM accounts ORDER BY platform, name"
            ).fetchall()
        return [self._account_dict(row) for row in rows]

    def get_account(self, account_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id, owner_user_id, name, platform, destination, options_json, created_at FROM accounts WHERE id = %s",
                (account_id,),
            ).fetchone()
        return self._account_dict(row) if row else None

    # ------------------------------------------------------------------
    # Mapping helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _row_to_content(row: dict[str, Any], media: list[MediaAsset]) -> ContentItem:
        return ContentItem(
            id=row["id"],
            title=row["title"],
            body=row["body"],
            cta=row["cta"],
            links=PostgresContentStore._json_value(row["links_json"], []),
            hashtags=PostgresContentStore._json_value(row["hashtags_json"], []),
            media=media,
            status=ContentStatus(row["status"]),
            metadata=PostgresContentStore._json_value(row["metadata_json"], {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_variant(row: dict[str, Any], media: list[MediaAsset]) -> PlatformVariant:
        return PlatformVariant(
            id=row["id"],
            content_id=row["content_id"],
            platform=row["platform"],
            title=row["title"],
            text=row["text"],
            media=media,
            fields=PostgresContentStore._json_value(row["fields_json"], {}),
            sync_with_master=bool(row["sync_with_master"]),
            revision=int(row["revision"]),
            metadata=PostgresContentStore._json_value(row["metadata_json"], {}),
        )

    @staticmethod
    def _row_to_publication(row: dict[str, Any]) -> Publication:
        return Publication(
            id=row["id"],
            variant_id=row["variant_id"],
            platform=row["platform"],
            account_id=int(row["account_id"]),
            destination=row["destination"],
            scheduled_at=row["scheduled_at"],
            status=PublicationStatus(row["status"]),
            external_post_id=row["external_post_id"],
            external_url=row["external_url"],
            published_at=row["published_at"],
            attempt_count=int(row["attempt_count"]),
            last_error_code=row["last_error_code"],
            last_error_message=row["last_error_message"],
            metadata=PostgresContentStore._json_value(row["metadata_json"], {}),
        )

    @staticmethod
    def _account_dict(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "owner_user_id": int(row["owner_user_id"]) if row["owner_user_id"] is not None else None,
            "name": row["name"],
            "platform": row["platform"],
            "destination": row["destination"],
            "options": PostgresContentStore._json_value(row["options_json"], {}),
            "created_at": PostgresContentStore._date_text(row["created_at"]),
        }

    @staticmethod
    def _json_value(value: Any, default: Any) -> Any:
        if value is None:
            return default
        if isinstance(value, (dict, list)):
            return value
        import json
        return json.loads(value)

    @staticmethod
    def _date_text(value: Any) -> str:
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    def _save_media_links(
        self,
        connection: Any,
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
                    id, workspace_id, source, media_type, alt_text, metadata_json, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(id) DO UPDATE SET
                    source = EXCLUDED.source,
                    media_type = EXCLUDED.media_type,
                    alt_text = EXCLUDED.alt_text,
                    metadata_json = EXCLUDED.metadata_json
                """,
                (
                    asset.id,
                    workspace_id,
                    asset.source,
                    asset.media_type,
                    asset.alt_text,
                    Jsonb(asset.metadata),
                    datetime.now(),
                ),
            )
            if content_id:
                connection.execute(
                    "INSERT INTO content_media(content_id, media_id, order_index) VALUES (%s, %s, %s)",
                    (content_id, asset.id, index),
                )
            if variant_id:
                connection.execute(
                    "INSERT INTO variant_media(variant_id, media_id, order_index) VALUES (%s, %s, %s)",
                    (variant_id, asset.id, index),
                )

    @staticmethod
    def _load_media(connection: Any, *, content_id: str | None = None, variant_id: str | None = None) -> list[MediaAsset]:
        if content_id:
            rows = connection.execute(
                """SELECT m.* FROM media_assets m
                JOIN content_media cm ON cm.media_id = m.id
                WHERE cm.content_id = %s ORDER BY cm.order_index""",
                (content_id,),
            ).fetchall()
        elif variant_id:
            rows = connection.execute(
                """SELECT m.* FROM media_assets m
                JOIN variant_media vm ON vm.media_id = m.id
                WHERE vm.variant_id = %s ORDER BY vm.order_index""",
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
                metadata=PostgresContentStore._json_value(row["metadata_json"], {}),
            )
            for row in rows
        ]


class PostgresWorkspaceStore:
    def __init__(self, base: PostgresContentStore, workspace_id: int) -> None:
        self.base = base
        self.workspace_id = workspace_id

    def get_workspace(self) -> dict[str, Any] | None:
        return self.base.get_workspace(self.workspace_id)

    def save_content(self, item: ContentItem, workspace_id: int | None = None) -> ContentItem:
        current = self.base.content_workspace_id(item.id)
        if current is not None and current != self.workspace_id:
            raise PermissionError("content belongs to another workspace")
        return self.base.save_content(item, workspace_id=self.workspace_id)

    def get_content(self, content_id: str) -> ContentItem | None:
        return self.base.get_content(content_id) if self.base.content_workspace_id(content_id) == self.workspace_id else None

    def list_content(self, *, limit: int = 100, offset: int = 0) -> list[ContentItem]:
        with self.base.connect() as connection:
            rows = connection.execute(
                "SELECT id FROM content_items WHERE workspace_id = %s ORDER BY updated_at DESC LIMIT %s OFFSET %s",
                (self.workspace_id, limit, offset),
            ).fetchall()
        return [item for row in rows if (item := self.base.get_content(row["id"])) is not None]

    def save_variant(self, variant: PlatformVariant) -> PlatformVariant:
        if self.base.content_workspace_id(variant.content_id) != self.workspace_id:
            raise PermissionError("variant belongs to another workspace")
        return self.base.save_variant(variant)

    def get_variant(self, variant_id: str) -> PlatformVariant | None:
        return self.base.get_variant(variant_id) if self.base.variant_workspace_id(variant_id) == self.workspace_id else None

    def get_variant_for_platform(self, content_id: str, platform: str) -> PlatformVariant | None:
        if self.base.content_workspace_id(content_id) != self.workspace_id:
            return None
        return self.base.get_variant_for_platform(content_id, platform)

    def list_variants(self, content_id: str) -> list[PlatformVariant]:
        return self.base.list_variants(content_id) if self.base.content_workspace_id(content_id) == self.workspace_id else []

    def save_publication(self, publication: Publication) -> Publication:
        if self.base.variant_workspace_id(publication.variant_id) != self.workspace_id:
            raise PermissionError("publication variant belongs to another workspace")
        if self.get_account(publication.account_id) is None:
            raise PermissionError("publication account is not available in this workspace")
        return self.base.save_publication(publication)

    def get_publication(self, publication_id: str) -> Publication | None:
        if self.base.publication_workspace_id(publication_id) != self.workspace_id:
            return None
        return self.base.get_publication(publication_id)

    def list_publications(self, *, status: PublicationStatus | None = None, limit: int = 100) -> list[Publication]:
        args: list[Any] = [self.workspace_id]
        extra = ""
        if status is not None:
            extra = " AND p.status = %s"
            args.append(status.value)
        args.append(limit)
        with self.base.connect() as connection:
            rows = connection.execute(
                f"""SELECT p.* FROM publications_v2 p
                JOIN platform_variants v ON v.id = p.variant_id
                JOIN content_items c ON c.id = v.content_id
                WHERE c.workspace_id = %s{extra}
                ORDER BY COALESCE(p.scheduled_at, p.created_at) DESC LIMIT %s""",
                tuple(args),
            ).fetchall()
        return [self.base._row_to_publication(row) for row in rows]

    def record_attempt(self, publication: Publication, result: PublicationResult) -> None:
        if self.base.publication_workspace_id(publication.id) != self.workspace_id:
            raise PermissionError("publication belongs to another workspace")
        self.base.record_attempt(publication, result)

    def list_accounts(self) -> list[dict[str, Any]]:
        with self.base.connect() as connection:
            rows = connection.execute(
                """SELECT DISTINCT a.id, a.owner_user_id, a.name, a.platform,
                a.destination, a.options_json, a.created_at
                FROM accounts a
                JOIN workspaces w ON w.id = %s
                LEFT JOIN workspace_accounts wa ON wa.account_id = a.id AND wa.workspace_id = w.id
                WHERE a.owner_user_id = w.owner_user_id OR wa.workspace_id IS NOT NULL
                ORDER BY a.platform, a.name""",
                (self.workspace_id,),
            ).fetchall()
        return [self.base._account_dict(row) for row in rows]

    def get_account(self, account_id: int) -> dict[str, Any] | None:
        with self.base.connect() as connection:
            row = connection.execute(
                """SELECT DISTINCT a.id, a.owner_user_id, a.name, a.platform,
                a.destination, a.options_json, a.created_at
                FROM accounts a
                JOIN workspaces w ON w.id = %s
                LEFT JOIN workspace_accounts wa ON wa.account_id = a.id AND wa.workspace_id = w.id
                WHERE a.id = %s
                AND (a.owner_user_id = w.owner_user_id OR wa.workspace_id IS NOT NULL)""",
                (self.workspace_id, account_id),
            ).fetchone()
        return self.base._account_dict(row) if row else None
