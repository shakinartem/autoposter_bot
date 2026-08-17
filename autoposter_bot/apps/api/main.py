from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from autoposter_bot.application.content import ContentApplication
from autoposter_bot.application.publishing import PublishingApplication
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
)
from autoposter_bot.config import load_settings
from autoposter_bot.domain.content import MediaAsset, Publication, PublicationStatus
from autoposter_bot.infrastructure.content_store import SQLiteContentStore
from autoposter_bot.platforms.factory import build_default_platform_registry


settings = load_settings()
store = SQLiteContentStore(settings.database_path)
registry = build_default_platform_registry(settings)
content_application = ContentApplication(store)
publishing_application = PublishingApplication(registry)


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.init_schema()
    yield


app = FastAPI(
    title="Autoposter Content OS API",
    version="0.2.0",
    description="Web/API-first content distribution backend for Autoposter.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthView)
def health() -> HealthView:
    return HealthView(status="ok", service="autoposter-api", version=app.version)


@app.get("/api/v1/platforms")
def list_platforms() -> dict[str, Any]:
    return {
        name: asdict(capability)
        for name, capability in registry.capabilities().items()
    }


@app.get("/api/v1/accounts", response_model=list[AccountView])
def list_accounts(platform: str | None = Query(default=None)) -> list[AccountView]:
    accounts = store.list_accounts()
    if platform:
        accounts = [item for item in accounts if item["platform"].lower() == platform.lower()]
    return [
        AccountView(
            id=item["id"],
            owner_user_id=item["owner_user_id"],
            name=item["name"],
            platform=item["platform"],
            destination=item["destination"],
            created_at=item["created_at"],
        )
        for item in accounts
    ]


@app.post("/api/v1/content", response_model=ContentView, status_code=201)
def create_content(payload: ContentCreate) -> ContentView:
    item = content_application.create(
        title=payload.title,
        body=payload.body,
        cta=payload.cta,
        links=payload.links,
        hashtags=payload.hashtags,
        media=[_media_from_payload(media) for media in payload.media],
        metadata=payload.metadata,
        workspace_id=payload.workspace_id,
    )
    return _content_view(item)


@app.get("/api/v1/content", response_model=list[ContentView])
def list_content(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[ContentView]:
    return [_content_view(item) for item in store.list_content(limit=limit, offset=offset)]


@app.get("/api/v1/content/{content_id}", response_model=ContentView)
def get_content(content_id: str) -> ContentView:
    item = store.get_content(content_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Content item not found")
    return _content_view(item)


@app.patch("/api/v1/content/{content_id}", response_model=ContentView)
def update_content(content_id: str, payload: ContentUpdate) -> ContentView:
    changes = payload.model_dump(exclude_unset=True)
    if "media" in changes:
        changes["media"] = [_media_from_payload(MediaPayload.model_validate(media)) for media in changes["media"] or []]
    for key in ("title", "body", "cta"):
        if key in changes and changes[key] is None:
            changes[key] = ""
    for key in ("links", "hashtags"):
        if key in changes and changes[key] is None:
            changes[key] = []
    if "metadata" in changes and changes["metadata"] is None:
        changes["metadata"] = {}

    item = content_application.update(content_id, **changes)
    if item is None:
        raise HTTPException(status_code=404, detail="Content item not found")
    return _content_view(item)


@app.get("/api/v1/content/{content_id}/variants", response_model=list[VariantView])
def list_variants(content_id: str) -> list[VariantView]:
    if store.get_content(content_id) is None:
        raise HTTPException(status_code=404, detail="Content item not found")
    return [_variant_view(variant) for variant in store.list_variants(content_id)]


@app.put("/api/v1/content/{content_id}/variants/{platform}", response_model=VariantView)
def upsert_variant(content_id: str, platform: str, payload: VariantUpsert) -> VariantView:
    try:
        registry.get(platform)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    changes = payload.model_dump(exclude_unset=True)
    if "media" in changes:
        changes["media"] = [_media_from_payload(MediaPayload.model_validate(media)) for media in changes["media"] or []]
    if "title" in changes and changes["title"] is None:
        changes["title"] = ""
    if "text" in changes and changes["text"] is None:
        changes["text"] = ""
    if "fields" in changes and changes["fields"] is None:
        changes["fields"] = {}
    if "metadata" in changes and changes["metadata"] is None:
        changes["metadata"] = {}

    variant = content_application.upsert_variant(content_id, platform, **changes)
    if variant is None:
        raise HTTPException(status_code=404, detail="Content item not found")
    return _variant_view(variant)


@app.get("/api/v1/variants/{variant_id}", response_model=VariantView)
def get_variant(variant_id: str) -> VariantView:
    variant = store.get_variant(variant_id)
    if variant is None:
        raise HTTPException(status_code=404, detail="Platform variant not found")
    return _variant_view(variant)


@app.post("/api/v1/variants/{variant_id}/publications", response_model=PublicationView, status_code=201)
def create_publication(variant_id: str, payload: PublicationCreate) -> PublicationView:
    variant = store.get_variant(variant_id)
    if variant is None:
        raise HTTPException(status_code=404, detail="Platform variant not found")
    account = store.get_account(payload.account_id)
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
        status=(PublicationStatus.SCHEDULED if payload.scheduled_at else PublicationStatus.DRAFT),
    )
    store.save_publication(publication)
    return _publication_view(publication)


@app.get("/api/v1/publications", response_model=list[PublicationView])
def list_publications(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[PublicationView]:
    parsed_status = None
    if status:
        try:
            parsed_status = PublicationStatus(status)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Unknown publication status: {status}") from exc
    return [_publication_view(item) for item in store.list_publications(status=parsed_status, limit=limit)]


@app.get("/api/v1/publications/{publication_id}", response_model=PublicationView)
def get_publication(publication_id: str) -> PublicationView:
    publication = store.get_publication(publication_id)
    if publication is None:
        raise HTTPException(status_code=404, detail="Publication not found")
    return _publication_view(publication)


@app.post("/api/v1/publications/{publication_id}/publish", response_model=PublishResultView)
def publish(publication_id: str, payload: PublishRequest) -> PublishResultView:
    publication = store.get_publication(publication_id)
    if publication is None:
        raise HTTPException(status_code=404, detail="Publication not found")
    variant = store.get_variant(publication.variant_id)
    if variant is None:
        raise HTTPException(status_code=409, detail="Publication variant no longer exists")
    account = store.get_account(publication.account_id)
    if account is None:
        raise HTTPException(status_code=409, detail="Publication social account no longer exists")

    try:
        result = publishing_application.publish(
            variant,
            publication,
            account_options=account["options"],
            dry_run=payload.dry_run,
        )
    except KeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if not payload.dry_run:
        store.save_publication(publication)
        store.record_attempt(publication, result)
    return PublishResultView(**asdict(result))


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
