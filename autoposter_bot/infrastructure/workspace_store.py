from __future__ import annotations

from typing import Any

from autoposter_bot.domain.content import ContentItem, PlatformVariant, Publication, PublicationStatus
from autoposter_bot.infrastructure.content_store import SQLiteContentStore
from autoposter_bot.platforms.base import PublicationResult


class WorkspaceContentStore:
    """Workspace-scoped persistence used only by request-facing API code."""

    def __init__(self, base: SQLiteContentStore, workspace_id: int) -> None:
        self.base = base
        self.workspace_id = workspace_id

    def get_workspace(self) -> dict[str, Any] | None:
        with self.base.connect() as db:
            row = db.execute(
                "SELECT id, name, owner_user_id, created_at FROM workspaces WHERE id = ?",
                (self.workspace_id,),
            ).fetchone()
        return dict(row) if row else None

    def save_content(self, item: ContentItem, workspace_id: int | None = None) -> ContentItem:
        current = self._content_workspace(item.id)
        if current is not None and current != self.workspace_id:
            raise PermissionError("content belongs to another workspace")
        return self.base.save_content(item, workspace_id=self.workspace_id)

    def get_content(self, content_id: str) -> ContentItem | None:
        return self.base.get_content(content_id) if self._content_workspace(content_id) == self.workspace_id else None

    def list_content(self, *, limit: int = 100, offset: int = 0) -> list[ContentItem]:
        with self.base.connect() as db:
            ids = db.execute(
                "SELECT id FROM content_items WHERE workspace_id = ? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (self.workspace_id, limit, offset),
            ).fetchall()
        return [item for row in ids if (item := self.base.get_content(row["id"])) is not None]

    def save_variant(self, variant: PlatformVariant) -> PlatformVariant:
        if self._content_workspace(variant.content_id) != self.workspace_id:
            raise PermissionError("variant belongs to another workspace")
        return self.base.save_variant(variant)

    def get_variant(self, variant_id: str) -> PlatformVariant | None:
        return self.base.get_variant(variant_id) if self._variant_visible(variant_id) else None

    def get_variant_for_platform(self, content_id: str, platform: str) -> PlatformVariant | None:
        if self._content_workspace(content_id) != self.workspace_id:
            return None
        return self.base.get_variant_for_platform(content_id, platform)

    def list_variants(self, content_id: str) -> list[PlatformVariant]:
        return self.base.list_variants(content_id) if self._content_workspace(content_id) == self.workspace_id else []

    def save_publication(self, publication: Publication) -> Publication:
        if not self._variant_visible(publication.variant_id) or self.get_account(publication.account_id) is None:
            raise PermissionError("publication references another workspace")
        return self.base.save_publication(publication)

    def get_publication(self, publication_id: str) -> Publication | None:
        return self.base.get_publication(publication_id) if self._publication_visible(publication_id) else None

    def list_publications(self, *, status: PublicationStatus | None = None, limit: int = 100) -> list[Publication]:
        args: list[Any] = [self.workspace_id]
        extra = ""
        if status is not None:
            extra = " AND p.status = ?"
            args.append(status.value)
        args.append(limit)
        with self.base.connect() as db:
            ids = db.execute(
                f"""SELECT p.id FROM publications_v2 p
                JOIN platform_variants v ON v.id = p.variant_id
                JOIN content_items c ON c.id = v.content_id
                WHERE c.workspace_id = ?{extra}
                ORDER BY COALESCE(p.scheduled_at, p.created_at) DESC LIMIT ?""",
                tuple(args),
            ).fetchall()
        return [item for row in ids if (item := self.base.get_publication(row["id"])) is not None]

    def record_attempt(self, publication: Publication, result: PublicationResult) -> None:
        if not self._publication_visible(publication.id):
            raise PermissionError("publication belongs to another workspace")
        self.base.record_attempt(publication, result)

    def list_accounts(self) -> list[dict[str, Any]]:
        with self.base.connect() as db:
            rows = db.execute(
                """SELECT DISTINCT a.id, a.owner_user_id, a.name, a.platform, a.destination, a.options_json, a.created_at
                FROM accounts a JOIN workspaces w ON w.id = ?
                LEFT JOIN workspace_accounts wa ON wa.account_id = a.id AND wa.workspace_id = w.id
                WHERE a.owner_user_id = w.owner_user_id OR wa.workspace_id IS NOT NULL
                ORDER BY a.platform, a.name""",
                (self.workspace_id,),
            ).fetchall()
        return [self.base._account_dict(row) for row in rows]

    def get_account(self, account_id: int) -> dict[str, Any] | None:
        for account in self.list_accounts():
            if account["id"] == account_id:
                return account
        return None

    def _content_workspace(self, content_id: str) -> int | None:
        with self.base.connect() as db:
            row = db.execute("SELECT workspace_id FROM content_items WHERE id = ?", (content_id,)).fetchone()
        return int(row["workspace_id"]) if row and row["workspace_id"] is not None else None

    def _variant_visible(self, variant_id: str) -> bool:
        with self.base.connect() as db:
            row = db.execute(
                """SELECT 1 FROM platform_variants v JOIN content_items c ON c.id = v.content_id
                WHERE v.id = ? AND c.workspace_id = ?""",
                (variant_id, self.workspace_id),
            ).fetchone()
        return row is not None

    def _publication_visible(self, publication_id: str) -> bool:
        with self.base.connect() as db:
            row = db.execute(
                """SELECT 1 FROM publications_v2 p JOIN platform_variants v ON v.id = p.variant_id
                JOIN content_items c ON c.id = v.content_id WHERE p.id = ? AND c.workspace_id = ?""",
                (publication_id, self.workspace_id),
            ).fetchone()
        return row is not None
