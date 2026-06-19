from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from autoposter_bot.video_models import (
    VideoAsset,
    VideoMediaType,
    VideoPost,
    VideoPostStatus,
    VideoTarget,
    VideoTargetStatus,
)
from autoposter_bot.video_validation import (
    ValidationIssue,
    ValidationSeverity,
    VideoPostValidationService,
)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class VideoDraftError(Exception):
    """Base exception for draft creation failures."""


class VideoDraftValidationError(VideoDraftError):
    """Raised when validation fails before saving a draft."""

    def __init__(self, message: str, issues: list[ValidationIssue]) -> None:
        self.issues = issues
        super().__init__(f"{message} ({len(issues)} issue(s))")


class VideoDraftNotFoundError(VideoDraftError):
    """Raised when a draft post id cannot be found."""


# ---------------------------------------------------------------------------
# Bundle dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class VideoDraftBundle:
    """A complete video draft with post, assets and targets."""
    post: VideoPost
    assets: list[VideoAsset] = field(default_factory=list)
    targets: list[VideoTarget] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Repository protocol  (duck-typing interface)
# ---------------------------------------------------------------------------


class _RepositoryProtocol:
    """Minimal interface expected from the database/repository object.

    The actual type is checked at runtime by duck typing.
    """
    def create_video_post(self, *, external_post_id: str, text: str,
                          title: str | None = None,
                          owner_user_id: int | None = None,
                          status: str = "draft",
                          scheduled_at: str | None = None,
                          published_at: str | None = None,
                          metadata: dict | None = None) -> int: ...

    def get_video_post(self, post_id: int) -> Any: ...

    def update_video_post_status(self, post_id: int, status: str,
                                 updated_at: str | None = None) -> bool: ...

    def create_video_asset(self, *, post_id: int, source: str,
                           media_type: str = "video",
                           order_index: int = 0,
                           original_filename: str | None = None,
                           file_size: int | None = None,
                           mime_type: str | None = None,
                           duration_seconds: float | None = None,
                           width: int | None = None,
                           height: int | None = None,
                           aspect_ratio: str | None = None,
                           cloudinary_public_id: str | None = None,
                           cloudinary_url: str | None = None,
                           processed: int = 0,
                           options: dict | None = None) -> int: ...

    def list_video_assets(self, post_id: int) -> list[Any]: ...

    def create_video_target(self, *, post_id: int, account_id: int,
                            platform: str,
                            destination: str | None = None,
                            options: dict | None = None) -> int: ...

    def list_video_targets(self, post_id: int) -> list[Any]: ...


# ---------------------------------------------------------------------------
# Draft Creation Service
# ---------------------------------------------------------------------------

# Temporary placeholder id used during pre-save validation.
# The post does not have a real id yet — the database will assign one.
_TEMP_VALIDATION_POST_ID = 999_999_999


class VideoDraftCreationService:
    """Creates, retrieves and manages video post drafts.

    The service is stateless regarding storage — all persistence is delegated
    to a repository / database object passed at construction time.
    Validation is delegated to ``VideoPostValidationService``.
    """

    def __init__(
        self,
        repository: _RepositoryProtocol,
        validation: VideoPostValidationService,
    ) -> None:
        self._repo = repository
        self._validation = validation

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_draft(
        self,
        *,
        owner_user_id: int | None = None,
        title: str | None = None,
        caption: str = "",
        assets: list[VideoAsset] | None = None,
        targets: list[VideoTarget] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> VideoPost:
        """Create a new video post draft.

        Steps:
        1. Build a transient VideoPost (with placeholder id for validation).
        2. Build transient VideoAssets and VideoTargets.
        3. Run validation.
        4. If OK — persist everything and return the stored VideoPost.
        5. If validation fails — raise ``VideoDraftValidationError`` without
           saving anything.
        """
        assets_list = list(assets) if assets else []
        targets_list = list(targets) if targets else []

        # Build the transient post with a placeholder id for validation
        # (the real id will be assigned by the database).
        post = VideoPost(
            id=_TEMP_VALIDATION_POST_ID,
            owner_user_id=owner_user_id,
            external_post_id=self._generate_external_id(),
            title=title,
            text=caption or "",
            status=VideoPostStatus.DRAFT.value,
        )
        if metadata:
            post.metadata = metadata

        # Build transient assets/targets linked to the placeholder id
        transient_assets = [
            self._make_transient_asset(a, idx, _TEMP_VALIDATION_POST_ID)
            for idx, a in enumerate(assets_list)
        ]
        transient_targets = [
            self._make_transient_target(t, _TEMP_VALIDATION_POST_ID)
            for t in targets_list
        ]

        # Validate before saving
        validation_result = self._validation.validate_post(
            post, transient_assets, transient_targets,
        )
        if validation_result.has_errors():
            raise VideoDraftValidationError(
                "Cannot create draft: validation failed",
                validation_result.errors(),
            )

        # Persist post — the database assigns the real id
        post_id = self._repo.create_video_post(
            owner_user_id=owner_user_id,
            external_post_id=post.external_post_id,
            title=title,
            text=caption or "",
            status=VideoPostStatus.DRAFT.value,
            metadata=metadata or {},
        )
        post.id = post_id

        # Persist assets
        for idx, asset_data in enumerate(assets_list):
            self._repo.create_video_asset(
                post_id=post_id,
                source=asset_data.source or "",
                media_type=asset_data.media_type or VideoMediaType.VIDEO.value,
                order_index=asset_data.order_index if asset_data.order_index else idx,
                original_filename=asset_data.original_filename,
                file_size=asset_data.file_size,
                mime_type=asset_data.mime_type,
                duration_seconds=asset_data.duration_seconds,
                width=asset_data.width,
                height=asset_data.height,
                aspect_ratio=asset_data.aspect_ratio,
                cloudinary_public_id=asset_data.cloudinary_public_id,
                cloudinary_url=asset_data.cloudinary_url,
                processed=asset_data.processed if asset_data.processed else 0,
                options=asset_data.options if hasattr(asset_data, 'options') and asset_data.options else None,
            )

        # Persist targets
        for target_data in targets_list:
            self._repo.create_video_target(
                post_id=post_id,
                account_id=target_data.account_id or 0,
                platform=target_data.platform or "",
                destination=target_data.destination,
                options=target_data.options if hasattr(target_data, 'options') and target_data.options else None,
            )

        return self._hydrate_post(post_id)

    def create_draft_from_payload(
        self,
        payload: dict[str, Any],
    ) -> VideoPost:
        """Create a draft from a dictionary payload.

        Expected payload structure::

            {
                "owner_user_id": int | None,
                "title": str | None,
                "caption": str,
                "assets": list[dict],   # see ``_asset_from_dict``
                "targets": list[dict],  # see ``_target_from_dict``
                "metadata": dict | None,
            }

        Returns the newly created VideoPost with id populated.
        """
        assets_data = [
            self._asset_from_dict(a) for a in (payload.get("assets") or [])
        ]
        targets_data = [
            self._target_from_dict(t) for t in (payload.get("targets") or [])
        ]

        return self.create_draft(
            owner_user_id=payload.get("owner_user_id"),
            title=payload.get("title"),
            caption=payload.get("caption", ""),
            assets=assets_data,
            targets=targets_data,
            metadata=payload.get("metadata"),
        )

    def add_asset(
        self,
        post_id: int,
        asset: VideoAsset,
    ) -> VideoAsset:
        """Add a single VideoAsset to an existing draft.

        The post must exist and be in ``draft`` status.
        No publication or queue action is triggered.
        """
        post = self._get_post_or_raise(post_id)
        if post.status != VideoPostStatus.DRAFT.value:
            raise VideoDraftError(
                f"Cannot add asset to post {post_id}: "
                f"status is {post.status!r}, expected 'draft'",
            )

        existing_assets = self._load_assets(post_id)
        next_order = max((a.order_index for a in existing_assets), default=-1) + 1

        asset_id = self._repo.create_video_asset(
            post_id=post_id,
            source=asset.source or "",
            media_type=asset.media_type or VideoMediaType.VIDEO.value,
            order_index=asset.order_index if asset.order_index else next_order,
            original_filename=asset.original_filename,
            file_size=asset.file_size,
            mime_type=asset.mime_type,
            duration_seconds=asset.duration_seconds,
            width=asset.width,
            height=asset.height,
            aspect_ratio=asset.aspect_ratio,
            cloudinary_public_id=asset.cloudinary_public_id,
            cloudinary_url=asset.cloudinary_url,
            processed=asset.processed if asset.processed else 0,
            options=asset.options if hasattr(asset, 'options') and asset.options else None,
        )

        out = VideoAsset(
            id=asset_id,
            post_id=post_id,
            source=asset.source or "",
            media_type=asset.media_type or VideoMediaType.VIDEO.value,
            order_index=asset.order_index if asset.order_index else next_order,
            original_filename=asset.original_filename,
            file_size=asset.file_size,
            mime_type=asset.mime_type,
            duration_seconds=asset.duration_seconds,
            width=asset.width,
            height=asset.height,
            aspect_ratio=asset.aspect_ratio,
            cloudinary_public_id=asset.cloudinary_public_id,
            cloudinary_url=asset.cloudinary_url,
            processed=asset.processed if asset.processed else 0,
        )
        return out

    def add_target(
        self,
        post_id: int,
        target: VideoTarget,
    ) -> VideoTarget:
        """Add a single VideoTarget to an existing draft.

        The post must exist and be in ``draft`` status.
        No publication or queue action is triggered.
        """
        post = self._get_post_or_raise(post_id)
        if post.status != VideoPostStatus.DRAFT.value:
            raise VideoDraftError(
                f"Cannot add target to post {post_id}: "
                f"status is {post.status!r}, expected 'draft'",
            )

        target_id = self._repo.create_video_target(
            post_id=post_id,
            account_id=target.account_id or 0,
            platform=target.platform or "",
            destination=target.destination,
            options=target.options if hasattr(target, 'options') and target.options else None,
        )

        out = VideoTarget(
            id=target_id,
            post_id=post_id,
            account_id=target.account_id or 0,
            platform=target.platform or "",
            destination=target.destination,
            status=VideoTargetStatus.PENDING.value,
        )
        return out

    def clone_draft(
        self,
        source_post_id: int,
        *,
        owner_user_id: int | None = None,
        title: str | None = None,
    ) -> VideoPost:
        """Deep-clone an existing draft.

        1. Load the original post, its assets and targets.
        2. Create a new VideoPost with a fresh external id.
        3. Copy all assets.
        4. Copy all targets.
        5. Return the new draft.
        """
        original = self._get_post_or_raise(source_post_id)
        assets = self._load_assets(source_post_id)
        targets = self._load_targets(source_post_id)

        new_external_id = self._generate_external_id()
        new_post_id = self._repo.create_video_post(
            owner_user_id=owner_user_id or original.owner_user_id,
            external_post_id=new_external_id,
            title=title or original.title,
            text=original.text,
            status=VideoPostStatus.DRAFT.value,
            metadata=original.metadata if hasattr(original, 'metadata') else {},
        )

        for asset in assets:
            self._repo.create_video_asset(
                post_id=new_post_id,
                source=asset.source or "",
                media_type=asset.media_type or VideoMediaType.VIDEO.value,
                order_index=asset.order_index,
                original_filename=asset.original_filename,
                file_size=asset.file_size,
                mime_type=asset.mime_type,
                duration_seconds=asset.duration_seconds,
                width=asset.width,
                height=asset.height,
                aspect_ratio=asset.aspect_ratio,
                cloudinary_public_id=asset.cloudinary_public_id,
                cloudinary_url=asset.cloudinary_url,
                processed=0,
            )

        for target in targets:
            self._repo.create_video_target(
                post_id=new_post_id,
                account_id=target.account_id,
                platform=target.platform,
                destination=target.destination,
                options=target.options if hasattr(target, 'options') and target.options else None,
            )

        return self._hydrate_post(new_post_id)

    def get_draft(self, post_id: int) -> VideoDraftBundle:
        """Return a complete VideoDraftBundle for a given post id.

        Raises VideoDraftNotFoundError if the post does not exist.
        """
        post = self._get_post_or_raise(post_id)
        assets = self._load_assets(post_id)
        targets = self._load_targets(post_id)
        return VideoDraftBundle(post=post, assets=assets, targets=targets)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_external_id() -> str:
        return f"draft-{uuid.uuid4().hex[:12]}"

    def _get_post_or_raise(self, post_id: int) -> VideoPost:
        row = self._repo.get_video_post(post_id)
        if row is None:
            raise VideoDraftNotFoundError(
                f"Video post with id {post_id} not found",
            )
        return _row_to_video_post(row)

    def _load_assets(self, post_id: int) -> list[VideoAsset]:
        rows = self._repo.list_video_assets(post_id)
        return [_row_to_video_asset(r) for r in rows]

    def _load_targets(self, post_id: int) -> list[VideoTarget]:
        rows = self._repo.list_video_targets(post_id)
        return [_row_to_video_target(r) for r in rows]

    def _hydrate_post(self, post_id: int) -> VideoPost:
        return self._get_post_or_raise(post_id)

    @staticmethod
    def _make_transient_asset(
        src: VideoAsset,
        order_index: int,
        temp_post_id: int = 0,
    ) -> VideoAsset:
        return VideoAsset(
            id=src.id,
            post_id=temp_post_id,
            source=src.source or "",
            media_type=src.media_type or VideoMediaType.VIDEO.value,
            order_index=src.order_index if src.order_index else order_index,
            original_filename=src.original_filename,
            file_size=src.file_size,
            mime_type=src.mime_type,
            duration_seconds=src.duration_seconds,
            width=src.width,
            height=src.height,
            aspect_ratio=src.aspect_ratio,
            cloudinary_public_id=src.cloudinary_public_id,
            cloudinary_url=src.cloudinary_url,
            processed=0,
        )

    @staticmethod
    def _make_transient_target(
        src: VideoTarget,
        temp_post_id: int = 0,
    ) -> VideoTarget:
        return VideoTarget(
            id=src.id,
            post_id=temp_post_id,
            account_id=src.account_id or 0,
            platform=src.platform or "",
            destination=src.destination,
            status=VideoTargetStatus.PENDING.value,
        )

    @staticmethod
    def _asset_from_dict(data: dict[str, Any]) -> VideoAsset:
        return VideoAsset(
            id=None,
            post_id=data.get("post_id", 0),
            source=data.get("source", "") or "",
            media_type=data.get("media_type", VideoMediaType.VIDEO.value),
            order_index=data.get("order_index", 0),
            original_filename=data.get("original_filename"),
            file_size=data.get("file_size"),
            mime_type=data.get("mime_type"),
            duration_seconds=data.get("duration_seconds"),
            width=data.get("width"),
            height=data.get("height"),
            aspect_ratio=data.get("aspect_ratio"),
            cloudinary_public_id=data.get("cloudinary_public_id"),
            cloudinary_url=data.get("cloudinary_url"),
            processed=data.get("processed", 0),
        )

    @staticmethod
    def _target_from_dict(data: dict[str, Any]) -> VideoTarget:
        return VideoTarget(
            id=None,
            post_id=data.get("post_id", 0),
            account_id=data.get("account_id", 0),
            platform=data.get("platform", "") or "",
            destination=data.get("destination"),
            status=VideoTargetStatus.PENDING.value,
        )


# ---------------------------------------------------------------------------
# Row → model helpers   (the repository returns sqlite3.Row-like objects)
# ---------------------------------------------------------------------------


def _row_to_video_post(row: Any) -> VideoPost:
    return VideoPost(
        id=int(row["id"]) if row["id"] is not None else None,
        owner_user_id=int(row["owner_user_id"]) if row["owner_user_id"] is not None else None,
        external_post_id=str(row["external_post_id"] or ""),
        title=row["title"],
        text=str(row["text"] or ""),
        status=str(row["status"] or ""),
        scheduled_at=row["scheduled_at"],
        published_at=row["published_at"],
        metadata_json=str(row["metadata_json"] or "{}"),
        created_at=str(row["created_at"] or ""),
        updated_at=str(row["updated_at"] or ""),
    )


def _row_to_video_asset(row: Any) -> VideoAsset:
    return VideoAsset(
        id=int(row["id"]) if row["id"] is not None else None,
        post_id=int(row["post_id"]),
        source=str(row["source"] or ""),
        media_type=str(row["media_type"] or ""),
        order_index=int(row["order_index"]),
        original_filename=row["original_filename"],
        file_size=int(row["file_size"]) if row["file_size"] is not None else None,
        mime_type=row["mime_type"],
        duration_seconds=float(row["duration_seconds"]) if row["duration_seconds"] is not None else None,
        width=int(row["width"]) if row["width"] is not None else None,
        height=int(row["height"]) if row["height"] is not None else None,
        aspect_ratio=row["aspect_ratio"],
        cloudinary_public_id=row["cloudinary_public_id"],
        cloudinary_url=row["cloudinary_url"],
        processed=int(row["processed"]),
        options_json=str(row["options_json"] or "{}"),
        created_at=str(row["created_at"] or ""),
        updated_at=str(row["updated_at"] or ""),
    )


def _row_to_video_target(row: Any) -> VideoTarget:
    return VideoTarget(
        id=int(row["id"]) if row["id"] is not None else None,
        post_id=int(row["post_id"]),
        account_id=int(row["account_id"]),
        platform=str(row["platform"] or ""),
        destination=row["destination"],
        status=str(row["status"] or ""),
        options_json=str(row["options_json"] or "{}"),
        created_at=str(row["created_at"] or ""),
        updated_at=str(row["updated_at"] or ""),
    )