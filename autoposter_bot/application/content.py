from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from autoposter_bot.domain.content import ContentItem, MediaAsset, PlatformVariant


class ContentStore(Protocol):
    def save_content(self, item: ContentItem, workspace_id: int | None = None) -> ContentItem: ...
    def get_content(self, content_id: str) -> ContentItem | None: ...
    def save_variant(self, variant: PlatformVariant) -> PlatformVariant: ...
    def get_variant_for_platform(self, content_id: str, platform: str) -> PlatformVariant | None: ...
    def list_variants(self, content_id: str) -> list[PlatformVariant]: ...


_UNSET = object()


class ContentApplication:
    """Master-content and platform-variant orchestration.

    A platform variant stays synced with master content until a user manually
    overrides it. Editing a synced master automatically updates those variants;
    locked/manual variants keep their platform-native edits.
    """

    def __init__(self, store: ContentStore) -> None:
        self.store = store

    def create(
        self,
        *,
        title: str,
        body: str,
        cta: str = "",
        links: list[str] | None = None,
        hashtags: list[str] | None = None,
        media: list[MediaAsset] | None = None,
        metadata: dict[str, Any] | None = None,
        workspace_id: int | None = None,
    ) -> ContentItem:
        item = ContentItem(
            title=title,
            body=body,
            cta=cta,
            links=links or [],
            hashtags=hashtags or [],
            media=media or [],
            metadata=metadata or {},
        )
        return self.store.save_content(item, workspace_id=workspace_id)

    def update(
        self,
        content_id: str,
        *,
        title: Any = _UNSET,
        body: Any = _UNSET,
        cta: Any = _UNSET,
        links: Any = _UNSET,
        hashtags: Any = _UNSET,
        media: Any = _UNSET,
        metadata: Any = _UNSET,
    ) -> ContentItem | None:
        item = self.store.get_content(content_id)
        if item is None:
            return None

        if title is not _UNSET:
            item.title = title
        if body is not _UNSET:
            item.body = body
        if cta is not _UNSET:
            item.cta = cta
        if links is not _UNSET:
            item.links = links
        if hashtags is not _UNSET:
            item.hashtags = hashtags
        if media is not _UNSET:
            item.media = media
        if metadata is not _UNSET:
            item.metadata = metadata
        item.updated_at = datetime.now()
        self.store.save_content(item)

        for variant in self.store.list_variants(content_id):
            if not variant.sync_with_master:
                continue
            variant.title = item.title
            variant.text = item.body
            variant.media = list(item.media)
            variant.revision += 1
            self.store.save_variant(variant)
        return item

    def upsert_variant(
        self,
        content_id: str,
        platform: str,
        *,
        title: Any = _UNSET,
        text: Any = _UNSET,
        media: Any = _UNSET,
        fields: Any = _UNSET,
        sync_with_master: Any = _UNSET,
        metadata: Any = _UNSET,
    ) -> PlatformVariant | None:
        item = self.store.get_content(content_id)
        if item is None:
            return None

        platform = platform.strip().lower()
        variant = self.store.get_variant_for_platform(content_id, platform)
        manual_override = any(value is not _UNSET for value in (title, text, media, fields))

        if variant is None:
            variant = PlatformVariant(
                content_id=item.id,
                platform=platform,
                title=item.title if title is _UNSET else title,
                text=item.body if text is _UNSET else text,
                media=list(item.media) if media is _UNSET else media,
                fields={} if fields is _UNSET else fields,
                sync_with_master=(not manual_override if sync_with_master is _UNSET else bool(sync_with_master)),
                metadata={} if metadata is _UNSET else metadata,
            )
        else:
            if title is not _UNSET:
                variant.title = title
            if text is not _UNSET:
                variant.text = text
            if media is not _UNSET:
                variant.media = media
            if fields is not _UNSET:
                variant.fields = fields
            if metadata is not _UNSET:
                variant.metadata = metadata
            if sync_with_master is not _UNSET:
                variant.sync_with_master = bool(sync_with_master)
                if variant.sync_with_master:
                    variant.title = item.title
                    variant.text = item.body
                    variant.media = list(item.media)
            elif manual_override:
                variant.sync_with_master = False
            variant.revision += 1

        return self.store.save_variant(variant)
