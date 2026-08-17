from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from autoposter_bot.application.content import ContentApplication
from autoposter_bot.application.publishing import PublishingApplication
from autoposter_bot.apps.api.accounts import build_accounts_router
from autoposter_bot.apps.api.auth import build_auth_router
from autoposter_bot.apps.api.authorization import require_minimum_role
from autoposter_bot.apps.api.media import build_media_router
from autoposter_bot.apps.api.oauth import build_oauth_router
from autoposter_bot.apps.api.schemas import (
    AccountView,
    ContentCreate,
    ContentUpdate,
    ContentView,
    HealthView,
    MediaPayload,
    PublicationCreate,
    PublicationView,
    PublishRequest,
    PublishResultView,
    VariantUpsert,
    VariantView,
    WorkspaceView,
)
from autoposter_bot.apps.api.security import (
    AuthContext,
    configure_session_resolver,
    get_auth_context,
)
from autoposter_bot.config import load_settings
from autoposter_bot.domain.content import MediaAsset, Publication, PublicationStatus
from autoposter_bot.infrastructure.media_storage import build_media_storage
from autoposter_bot.infrastructure.persistence import build_persistence
from autoposter_bot.integrations.credential_refresh import CredentialRefreshService
from autoposter_bot.integrations.instagram_oauth import InstagramOAuthProvider
from autoposter_bot.integrations.tiktok_oauth import TikTokOAuthProvider
from autoposter_bot.platforms.factory import build_default_platform_registry


settings = load_settings()
persistence = build_persistence(settings)
media_storage = build_media_storage(settings)
registry = build_default_platform_registry(settings)
publishing_application = PublishingApplication(registry)
tiktok_oauth = TikTokOAuthProvider(settings)
instagram_oauth = InstagramOAuthProvider()
credential_refresh = CredentialRefreshService(tiktok=tiktok_oauth, instagram=instagram_oauth)
CurrentAuth = Annotated[AuthContext, Depends(get_auth_context)]


@asynccontextmanager
async def lifespan(_: FastAPI):
    persistence.init_schema()
    configure_session_resolver(persistence.auth.resolve_session)
    try:
        yield
    finally:
        configure_session_resolver(None)
        persistence.close()


def _cors_origins() -> list[str]:
    raw = os.getenv("AUTOPOSTER_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    return [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]


app = FastAPI(
    title="Autoposter Content OS API",
    version="0.9.0",
    description=f"Workspace-scoped web/API backend for Autoposter ({persistence.backend}).",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(build_auth_router(persistence.auth))
app.include_router(
    build_media_router(
        media_storage,
        workspace_exists=lambda workspace_id: persistence.scoped(workspace_id).get_workspace() is not None,
    )
)
app.include_router(
    build_accounts_router(
        store_for_workspace=persistence.scoped,
        platforms=registry.platforms(),
    )
)
app.include_router(
    build_oauth_router(
        providers={
            "tiktok": tiktok_oauth,
            "instagram": instagram_oauth,
        },
        store_for_workspace=persistence.scoped,
    )
)


@app.get("/health", response_model=HealthView)
def health() -> HealthView:
    return HealthView(status="ok", service="autoposter-api", version=app.version)


def _store(auth: AuthContext) -> Any:
    scoped = persistence.scoped(auth.workspace_id)
    if scoped.get_workspace() is None:
        raise HTTPException(status_code=403, detail=f"Workspace {auth.workspace_id} is not initialized")
    return scoped


@app.get("/api/v1/workspace", response_model=WorkspaceView)
def current_workspace(auth: CurrentAuth) -> WorkspaceView:
    workspace = _store(auth).get_workspace()
    assert workspace is not None
    return WorkspaceView(**workspace)


@app.get("/api/v1/platforms")
def list_platforms(auth: CurrentAuth) -> dict[str, Any]:
    _store(auth)
    return {name: asdict(capability) for name, capability in registry.capabilities().items()}


@app.get("/api/v1/accounts", response_model=list[AccountView])
def list_accounts(
    auth: CurrentAuth,
    platform: str | None = Query(default=None),
) -> list[AccountView]:
    accounts = _store(auth).list_accounts()
    if platform:
        accounts = [item for item in accounts if item["platform"].lower() == platform.lower()]
    return [
        AccountView(
            id=item["id"],
            owner_user_id=item["owner_user_id"],
            name=item["name"],
            platform=item["platform"],
            destination=item["destination"],
            public_options=_public_account_options(item.get("options") or {}),
            created_at=item["created_at"],
        )
        for item in accounts
    ]


@app.post("/api/v1/content", response_model=ContentView, status_code=201)
def create_content(payload: ContentCreate, auth: CurrentAuth) -> ContentView:
    require_minimum_role(auth.role, "editor")
    scoped = _store(auth)
    item = ContentApplication(scoped).create(
        title=payload.title,
        body=payload.body,
        cta=payload.cta,
        links=payload.links,
        hashtags=payload.hashtags,
        media=[_media_from_payload(media) for media in payload.media],
        metadata=payload.metadata,
        workspace_id=auth.workspace_id,
    )
    return _content_view(item)


@app.get("/api/v1/content", response_model=list[ContentView])
def list_content(
    auth: CurrentAuth,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[ContentView]:
    return [_content_view(item) for item in _store(auth).list_content(limit=limit, offset=offset)]


@app.get("/api/v1/content/{content_id}", response_model=ContentView)
def get_content(content_id: str, auth: CurrentAuth) -> ContentView:
    item = _store(auth).get_content(content_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Content item not found")
    return _content_view(item)


@app.patch("/api/v1/content/{content_id}", response_model=ContentView)
def update_content(content_id: str, payload: ContentUpdate, auth: CurrentAuth) -> ContentView:
    require_minimum_role(auth.role, "editor")
    scoped = _store(auth)
    changes = payload.model_dump(exclude_unset=True)
    if "media" in changes:
        changes["media"] = [
            _media_from_payload(MediaPayload.model_validate(media)) for media in changes["media"] or []
        ]
    for key in ("title", "body", "cta"):
        if key in changes and changes[key] is None:
            changes[key] = ""
    for key in ("links", "hashtags"):
        if key in changes and changes[key] is None:
            changes[key] = []
    if "metadata" in changes and changes["metadata"] is None:
        changes["metadata"] = {}
    item = ContentApplication(scoped).update(content_id, **changes)
    if item is None:
        raise HTTPException(status_code=404, detail="Content item not found")
    return _content_view(item)


@app.get("/api/v1/content/{content_id}/variants", response_model=list[VariantView])
def list_variants(content_id: str, auth: CurrentAuth) -> list[VariantView]:
    scoped = _store(auth)
    if scoped.get_content(content_id) is None:
        raise HTTPException(status_code=404, detail="Content item not found")
    return [_variant_view(variant) for variant in scoped.list_variants(content_id)]


@app.put("/api/v1/content/{content_id}/variants/{platform}", response_model=VariantView)
def upsert_variant(
    content_id: str,
    platform: str,
    payload: VariantUpsert,
    auth: CurrentAuth,
) -> VariantView:
    require_minimum_role(auth.role, "editor")
    scoped = _store(auth)
    try:
        registry.get(platform)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    changes = payload.model_dump(exclude_unset=True)
    if "media" in changes:
        changes["media"] = [
            _media_from_payload(MediaPayload.model_validate(media)) for media in changes["media"] or []
        ]
    if "title" in changes and changes["title"] is None:
        changes["title"] = ""
    if "text" in changes and changes["text"] is None:
        changes["text"] = ""
    if "fields" in changes and changes["fields"] is None:
        changes["fields"] = {}
    if "metadata" in changes and changes["metadata"] is None:
        changes["metadata"] = {}
    variant = ContentApplication(scoped).upsert_variant(content_id, platform, **changes)
    if variant is None:
        raise HTTPException(status_code=404, detail="Content item not found")
    return _variant_view(variant)


@app.get("/api/v1/variants/{variant_id}", response_model=VariantView)
def get_variant(variant_id: str, auth: CurrentAuth) -> VariantView:
    variant = _store(auth).get_variant(variant_id)
    if variant is None:
        raise HTTPException(status_code=404, detail="Platform variant not found")
    return _variant_view(variant)


@app.post("/api/v1/variants/{variant_id}/publications", response_model=PublicationView, status_code=201)
def create_publication(
    variant_id: str,
    payload: PublicationCreate,
    auth: CurrentAuth,
) -> PublicationView:
    require_minimum_role(auth.role, "editor")
    scoped = _store(auth)
    variant = scoped.get_variant(variant_id)
    if variant is None:
        raise HTTPException(status_code=404, detail="Platform variant not found")
    account = scoped.get_account(payload.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Social account not found")
    if account["platform"].lower() != variant.platform.lower():
        raise HTTPException(
            status_code=409,
            detail=f"Account platform {account['platform']} does not match variant platform {variant.platform}",
        )
    publication = Publication(
        variant_id=variant.id,
        platform=variant.platform,
        account_id=payload.account_id,
        destination=payload.destination or account["destination"],
        scheduled_at=payload.scheduled_at,
        status=PublicationStatus.SCHEDULED if payload.scheduled_at else PublicationStatus.DRAFT,
    )
    scoped.save_publication(publication)
    return _publication_view(publication)


@app.get("/api/v1/publications", response_model=list[PublicationView])
def list_publications(
    auth: CurrentAuth,
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[PublicationView]:
    parsed_status = None
    if status:
        try:
            parsed_status = PublicationStatus(status)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Unknown publication status: {status}") from exc
    return [
        _publication_view(item)
        for item in _store(auth).list_publications(status=parsed_status, limit=limit)
    ]


@app.get("/api/v1/publications/{publication_id}", response_model=PublicationView)
def get_publication(publication_id: str, auth: CurrentAuth) -> PublicationView:
    publication = _store(auth).get_publication(publication_id)
    if publication is None:
        raise HTTPException(status_code=404, detail="Publication not found")
    return _publication_view(publication)


@app.post("/api/v1/publications/{publication_id}/publish", response_model=PublishResultView)
def publish(publication_id: str, payload: PublishRequest, auth: CurrentAuth) -> PublishResultView:
    require_minimum_role(auth.role, "editor")
    scoped = _store(auth)
    publication = scoped.get_publication(publication_id)
    if publication is None:
        raise HTTPException(status_code=404, detail="Publication not found")
    variant = scoped.get_variant(publication.variant_id)
    if variant is None:
        raise HTTPException(status_code=409, detail="Publication variant no longer exists")
    account = scoped.get_account(publication.account_id)
    if account is None:
        raise HTTPException(status_code=409, detail="Publication social account no longer exists")

    try:
        refreshed_options, changed = credential_refresh.refresh_if_needed(
            publication.platform,
            account["options"],
        )
        if changed:
            refreshed_account = scoped.update_account(
                publication.account_id,
                options=refreshed_options,
            )
            if refreshed_account is not None:
                account = refreshed_account
        result = publishing_application.publish(
            variant,
            publication,
            account_options=account["options"],
            dry_run=payload.dry_run,
        )
    except KeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if not payload.dry_run:
        scoped.save_publication(publication)
        scoped.record_attempt(publication, result)
    return PublishResultView(**asdict(result))


def _public_account_options(options: dict[str, Any]) -> dict[str, Any]:
    secret_names = {
        "token",
        "access_token",
        "refresh_token",
        "bot_token",
        "api_key",
        "api_secret",
        "client_secret",
        "password",
        "secret",
    }
    return {
        key: value
        for key, value in options.items()
        if key.lower() not in secret_names
        and not key.lower().endswith(("_token", "_secret", "_password", "_api_key", "_api_secret"))
    }


def _media_from_payload(payload: MediaPayload) -> MediaAsset:
    kwargs = {
        "source": payload.source,
        "media_type": payload.media_type,
        "alt_text": payload.alt_text,
        "metadata": payload.metadata,
    }
    if payload.id:
        kwargs["id"] = payload.id
    return MediaAsset(**kwargs)


def _media_view(media: MediaAsset) -> MediaPayload:
    return MediaPayload(
        id=media.id,
        source=media.source,
        media_type=media.media_type,
        alt_text=media.alt_text,
        metadata=media.metadata,
    )


def _content_view(item) -> ContentView:
    return ContentView(
        id=item.id,
        title=item.title,
        body=item.body,
        cta=item.cta,
        links=item.links,
        hashtags=item.hashtags,
        media=[_media_view(media) for media in item.media],
        status=item.status.value,
        metadata=item.metadata,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _variant_view(variant) -> VariantView:
    return VariantView(
        id=variant.id,
        content_id=variant.content_id,
        platform=variant.platform,
        title=variant.title,
        text=variant.text,
        media=[_media_view(media) for media in variant.media],
        fields=variant.fields,
        sync_with_master=variant.sync_with_master,
        revision=variant.revision,
        metadata=variant.metadata,
    )


def _publication_view(publication: Publication) -> PublicationView:
    return PublicationView(
        id=publication.id,
        variant_id=publication.variant_id,
        platform=publication.platform,
        account_id=publication.account_id,
        destination=publication.destination,
        scheduled_at=publication.scheduled_at,
        status=publication.status.value,
        external_post_id=publication.external_post_id,
        external_url=publication.external_url,
        published_at=publication.published_at,
        attempt_count=publication.attempt_count,
        last_error_code=publication.last_error_code,
        last_error_message=publication.last_error_message,
        metadata=publication.metadata,
    )
