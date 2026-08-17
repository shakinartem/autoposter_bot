from autoposter_bot.apps.api.main import app


def _paths() -> set[str]:
    """Return effective HTTP paths, including lazily included routers.

    FastAPI 0.137+ keeps included routers as nested route objects instead of
    flattening every child into ``app.routes``. OpenAPI is the stable public
    representation of the composed HTTP surface and catches missing mounts
    without depending on FastAPI's internal router tree shape.
    """

    return set(app.openapi().get("paths", {}))


def test_oauth_connection_routes_are_mounted_in_fastapi_app():
    paths = _paths()

    assert "/api/v1/oauth/providers" in paths
    assert "/api/v1/oauth/{platform}/start" in paths
    assert "/api/v1/oauth/{platform}/callback" in paths


def test_account_media_and_publish_routes_remain_mounted_with_oauth_layer():
    paths = _paths()

    assert "/api/v1/accounts" in paths
    assert "/api/v1/account-connections" in paths
    assert "/api/v1/media" in paths
    assert "/api/v1/publications/{publication_id}/publish" in paths
