from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from autoposter_bot.apps.api.security import AuthContext, get_auth_context
from autoposter_bot.infrastructure.analytics_store import AnalyticsStore


CurrentAuth = Annotated[AuthContext, Depends(get_auth_context)]


def build_analytics_router(
    *,
    analytics: AnalyticsStore,
    store_for_workspace: Any,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["analytics"])

    @router.get("/publications/{publication_id}/analytics")
    def publication_analytics(publication_id: str, auth: CurrentAuth) -> list[dict[str, Any]]:
        scoped = store_for_workspace(auth.workspace_id)
        if scoped.get_publication(publication_id) is None:
            # Preserve tenant boundary: a foreign publication looks identical to
            # a nonexistent one instead of leaking that another workspace owns it.
            raise HTTPException(status_code=404, detail="Publication not found")
        snapshots = analytics.list_for_publication(
            workspace_id=auth.workspace_id,
            publication_id=publication_id,
        )
        return [
            {
                **asdict(snapshot),
                "captured_at": snapshot.captured_at.isoformat(),
            }
            for snapshot in snapshots
        ]

    return router
