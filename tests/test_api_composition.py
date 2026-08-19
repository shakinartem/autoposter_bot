from autoposter_bot.apps.api.main import app


def _paths() -> set[str]:
    """Return effective HTTP paths, including lazily included routers.

    FastAPI 0.137+ keeps included routers as nested route objects instead of
    flattening every child into ``app.routes``. OpenAPI is the stable public
    representation of the composed HTTP surface and catches missing mounts
    without depending on FastAPI's internal router tree shape.
    """

    return set(app.openapi().get("paths", {}))


def test_public_login_social_oauth_and_invitation_routes_are_mounted():
    paths = _paths()

    assert "/auth/providers" in paths
    assert "/auth/telegram/start" in paths
    assert "/auth/telegram/callback" in paths
    assert "/auth/login-grant/exchange" in paths
    assert "/auth/invitations/preview" in paths
    assert "/api/v1/oauth/providers" in paths
    assert "/api/v1/oauth/{platform}/start" in paths
    assert "/api/v1/oauth/{platform}/callback" in paths


def test_auth_team_analytics_account_media_and_publish_routes_remain_mounted():
    paths = _paths()

    assert "/api/v1/auth/me" in paths
    assert "/api/v1/auth/session" in paths
    assert "/api/v1/auth/workspaces" in paths
    assert "/api/v1/auth/workspaces/{workspace_id}/switch" in paths
    assert "/api/v1/members" in paths
    assert "/api/v1/invitations" in paths
    assert "/api/v1/invitations/{invitation_id}" in paths
    assert "/api/v1/invitations/accept" in paths
    assert "/api/v1/publications/{publication_id}/analytics" in paths
    assert "/api/v1/accounts" in paths
    assert "/api/v1/account-connections" in paths
    assert "/api/v1/media" in paths
    assert "/api/v1/publications/{publication_id}/publish" in paths
    assert "/api/v1/operations/overview" in paths
    assert "/api/v1/operations/reconciliation" in paths
    assert "/api/v1/operations/reconciliation/{publication_id}/resolve" in paths
    assert "/api/v1/operations/events" in paths
