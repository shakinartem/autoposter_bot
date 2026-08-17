from __future__ import annotations

import os
import tempfile
from typing import Annotated, Callable

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from autoposter_bot.apps.api.schemas import MediaPayload
from autoposter_bot.apps.api.security import AuthContext, get_auth_context
from autoposter_bot.domain.content import MediaAsset
from autoposter_bot.infrastructure.media_storage import MediaStorage


CurrentAuth = Annotated[AuthContext, Depends(get_auth_context)]


def build_media_router(
    storage: MediaStorage,
    *,
    workspace_exists: Callable[[int], bool],
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/media", tags=["media"])

    @router.post("", response_model=MediaPayload, status_code=201)
    async def upload_media(
        auth: CurrentAuth,
        file: UploadFile = File(...),
    ) -> MediaPayload:
        if not workspace_exists(auth.workspace_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Workspace {auth.workspace_id} is not initialized",
            )

        filename = file.filename or "media.bin"
        content_type = file.content_type or "application/octet-stream"
        if not content_type.startswith(("image/", "video/")):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Autoposter currently accepts image and video uploads",
            )

        max_bytes = max(1, int(os.getenv("AUTOPOSTER_MAX_UPLOAD_MB", "512"))) * 1024 * 1024
        total = 0
        try:
            with tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024) as buffer:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise HTTPException(
                            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail=f"Media file exceeds {max_bytes // (1024 * 1024)} MB limit",
                        )
                    buffer.write(chunk)
                buffer.seek(0)
                stored = storage.put(
                    workspace_id=auth.workspace_id,
                    filename=filename,
                    fileobj=buffer,
                    content_type=content_type,
                )
        finally:
            await file.close()

        asset = MediaAsset(
            source=stored.source,
            media_type=stored.media_type,
            metadata={
                "storage_backend": storage.backend,
                "storage_key": stored.storage_key,
                "content_type": stored.content_type,
                "original_name": stored.original_name,
                "size_bytes": total,
            },
        )
        return MediaPayload(
            id=asset.id,
            source=asset.source,
            media_type=asset.media_type,
            alt_text=asset.alt_text,
            metadata=asset.metadata,
        )

    return router
