from __future__ import annotations

from typing import Annotated, Any, Callable

from fastapi import APIRouter, Depends, HTTPException, status

from autoposter_bot.apps.api.authorization import require_minimum_role
from autoposter_bot.apps.api.schemas import AccountCreate, AccountUpdate, AccountView
from autoposter_bot.apps.api.security import AuthContext, get_auth_context
from autoposter_bot.platforms.account_specs import get_account_connection_specs


CurrentAuth = Annotated[AuthContext, Depends(get_auth_context)]


def build_accounts_router(
    *,
    store_for_workspace: Callable[[int], Any],
    platforms: tuple[str, ...],
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["accounts"])

    def scoped(workspace_id: int) -> Any:
        store = store_for_workspace(workspace_id)
        if store.get_workspace() is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Workspace {workspace_id} is not initialized",
            )
        return store

    @router.get("/account-connections")
    def connection_specs(auth: CurrentAuth) -> dict[str, Any]:
        scoped(auth.workspace_id)
        return get_account_connection_specs(platforms)

    @router.post("/accounts", response_model=AccountView, status_code=201)
    def create_account(payload: AccountCreate, auth: CurrentAuth) -> AccountView:
        require_minimum_role(auth.role, "admin")
        platform = payload.platform.strip().lower()
        specs = get_account_connection_specs(platforms)
        spec = specs.get(platform)
        if spec is None:
            raise HTTPException(status_code=404, detail=f"Unsupported platform: {platform}")
        _validate_connection_payload(
            spec,
            destination=payload.destination,
            options=payload.options,
            partial=False,
        )
        try:
            account = scoped(auth.workspace_id).create_account(
                name=payload.name,
                platform=platform,
                destination=payload.destination,
                options=payload.options,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _account_view(account)

    @router.patch("/accounts/{account_id}", response_model=AccountView)
    def update_account(account_id: int, payload: AccountUpdate, auth: CurrentAuth) -> AccountView:
        require_minimum_role(auth.role, "admin")
        store = scoped(auth.workspace_id)
        existing = store.get_account(account_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Social account not found")
        spec = get_account_connection_specs(platforms).get(existing["platform"].lower())
        if spec is None:
            raise HTTPException(status_code=409, detail="Account platform is no longer supported")
        if payload.options is not None or payload.destination is not None:
            _validate_connection_payload(
                spec,
                destination=payload.destination,
                options=payload.options or {},
                partial=True,
            )
        try:
            account = store.update_account(
                account_id,
                name=payload.name,
                destination=payload.destination,
                options=payload.options,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if account is None:
            raise HTTPException(status_code=404, detail="Social account not found")
        return _account_view(account)

    return router


def _validate_connection_payload(
    spec: dict[str, Any],
    *,
    destination: str | None,
    options: dict[str, Any],
    partial: bool,
) -> None:
    destination_spec = spec.get("destination") or {}
    if not partial and destination_spec.get("required") and not (destination or "").strip():
        raise HTTPException(status_code=422, detail=f"{destination_spec.get('label', 'Destination')} is required")

    fields = spec.get("fields") or {}
    unknown = sorted(set(options) - set(fields))
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported account options for {spec.get('platform')}: {', '.join(unknown)}",
        )
    if partial:
        return
    for name, field_spec in fields.items():
        if field_spec.get("required") and options.get(name) in (None, ""):
            raise HTTPException(
                status_code=422,
                detail=f"{field_spec.get('label', name)} is required",
            )


def _account_view(item: dict[str, Any]) -> AccountView:
    public_options = {
        key: value
        for key, value in dict(item.get("options") or {}).items()
        if key not in {"token", "access_token", "refresh_token", "bot_token", "api_key", "api_secret", "client_secret", "password", "secret"}
        and not key.endswith(("_token", "_secret", "_password", "_api_key", "_api_secret"))
    }
    return AccountView(
        id=int(item["id"]),
        owner_user_id=item.get("owner_user_id"),
        name=item["name"],
        platform=item["platform"],
        destination=item.get("destination"),
        public_options=public_options,
        created_at=item["created_at"],
    )
