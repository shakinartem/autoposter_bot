from autoposter_bot.apps.api.main import app


def test_oauth_connection_routes_are_mounted_in_fastapi_app():
    paths = {getattr(route, "path", "") for route in app.routes}

    assert "/api/v1/oauth/providers" in paths
    assert "/api/v1/oauth/{platform}/start" in paths
    assert "/api/v1/oauth/{platform}/callback" in paths


def test_account_and_publish_routes_remain_mounted_with_oauth_layer():
    paths = {getattr(route, "path", "") for route in app.routes}

    assert "/api/v1/accounts" in paths
    assert "/api/v1/account-connections" in paths
    assert "/api/v1/publications/{publication_id}/publish" in paths
