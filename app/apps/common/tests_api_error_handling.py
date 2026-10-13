from unittest.mock import patch

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from rest_framework import status
from rest_framework.test import APIClient

from apps.common.throttles import LoginRateThrottle


@pytest.mark.django_db
def test_unknown_api_route_uses_the_safe_json_error_envelope():
    response = APIClient().get("/api/v1/not-a-real-route/", HTTP_X_REQUEST_ID="api-404-regression")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response["Content-Type"].startswith("application/json")
    assert response.json() == {
        "success": False,
        "code": "NOT_FOUND",
        "message": "Not found",
        "errors": {},
        "request_id": "api-404-regression",
    }


@pytest.mark.django_db
def test_login_returns_controlled_503_when_redis_throttling_is_unavailable():
    with patch.object(LoginRateThrottle.cache, "get", side_effect=RedisConnectionError("unavailable")):
        response = APIClient().post(
            "/api/v1/auth/login/",
            {"identifier": "invalid@example.test", "password": "invalid-password"},
            format="json",
            HTTP_X_REQUEST_ID="login-redis-regression",
        )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response["Content-Type"].startswith("application/json")
    assert response.json() == {
        "success": False,
        "code": "SERVICE_DEPENDENCY_UNAVAILABLE",
        "message": "A required service dependency is unavailable.",
        "errors": {},
        "request_id": "login-redis-regression",
    }


@pytest.mark.django_db
def test_unexpected_login_error_never_leaks_as_html_or_a_traceback():
    with patch("apps.accounts.views.feature_enabled_or_raise", side_effect=RuntimeError("internal detail")):
        response = APIClient().post(
            "/api/v1/auth/login/",
            {"identifier": "invalid@example.test", "password": "invalid-password"},
            format="json",
            HTTP_X_REQUEST_ID="login-500-regression",
        )

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert response["Content-Type"].startswith("application/json")
    assert response.json() == {
        "success": False,
        "code": "INTERNAL_SERVER_ERROR",
        "message": "Server error",
        "errors": {},
        "request_id": "login-500-regression",
    }
    assert b"internal detail" not in response.content


@pytest.fixture
def reload_urls():
    import importlib

    import config.urls
    from django.urls import clear_url_caches

    def _reload():
        clear_url_caches()
        importlib.reload(config.urls)

    yield _reload
    # Leave the URL conf as the active settings define it for the following tests.
    _reload()


@pytest.mark.django_db
def test_admin_site_is_not_mounted_when_admin_is_disabled(settings, reload_urls):
    settings.DEBUG = False
    settings.ADMIN_ENABLED = False
    reload_urls()

    for path in ("/admin/", "/admin/login/", "/admin/accounts/user/"):
        response = APIClient().get(path, HTTP_X_REQUEST_ID="admin-404")
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response["Content-Type"].startswith("application/json")
        assert response.json()["code"] == "NOT_FOUND"
        assert response.json()["request_id"] == "admin-404"


@pytest.mark.django_db
def test_admin_site_is_mounted_when_admin_is_enabled(settings, reload_urls):
    settings.ADMIN_ENABLED = True
    reload_urls()

    response = APIClient().get("/admin/")
    assert response.status_code == status.HTTP_302_FOUND
    assert response["Location"].startswith("/admin/login/")


def test_production_defaults_keep_admin_disabled():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "config" / "settings" / "production.py").read_text(encoding="utf-8")
    assert 'ADMIN_ENABLED = get_bool_env("ADMIN_ENABLED", default=False)' in source
