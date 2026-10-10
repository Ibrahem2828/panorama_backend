from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.http import Http404
from rest_framework.test import APIClient

from apps.lectures.views import _viewer_token
from apps.product.models import MaintenanceMode


@pytest.mark.django_db
def test_login_is_reachable_during_maintenance_so_staff_can_switch_it_off():
    MaintenanceMode.objects.create(enabled=True, message_en="Maintenance", retry_after_seconds=30)
    client = APIClient()
    login = client.post("/api/v1/auth/login/", {"identifier": "x@example.test", "password": "nope"}, format="json")
    assert login.status_code == 400  # reached the view (bad credentials), not a 503 from the middleware
    # The web apps read /auth/me/ right after login to build the session, so it stays reachable as well.
    assert client.get("/api/v1/auth/me/").status_code == 401  # reached the view (no credentials), not a 503
    assert client.get("/api/v1/announcements/").status_code == 503
    assert client.post("/api/v1/auth/register/normal/", {}, format="json").status_code == 503


def test_viewer_session_header_must_be_a_uuid():
    good = str(uuid4())
    assert _viewer_token(SimpleNamespace(headers={"X-Viewer-Session": f" {good} "})) == good
    for bad in ("", "abc", "1; DROP TABLE", "00000000-0000"):
        with pytest.raises(Http404):
            _viewer_token(SimpleNamespace(headers={"X-Viewer-Session": bad}))


@pytest.mark.django_db
def test_account_deletion_is_available_by_default_and_can_be_switched_off():
    from apps.product.models import FeatureFlag
    from apps.product.services import FeatureFlagService

    assert FeatureFlagService.is_enabled("account_deletion_enabled") is True
    FeatureFlag.objects.create(key="account_deletion_enabled", enabled=False)
    from django.core.cache import cache

    cache.clear()
    assert FeatureFlagService.is_enabled("account_deletion_enabled") is False
