from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.accounts.authentication import SessionVersionRefreshToken
from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.product.models import MaintenanceMode, MobileAppReleasePolicy, PrivacyPolicyVersion, TermsVersion

pytestmark = pytest.mark.django_db

PROTECTED = "/api/v1/announcements/"


def _user(role: str, n: int) -> User:
    return User.objects.create_user(
        full_name=f"User {role}",
        email=f"{role}-{n}@example.test",
        phone_number=f"+96395560{n:04d}",
        password="StrongPass123!",
        role=role,
    )


def _client(user: User | None = None, token: str | None = None) -> APIClient:
    client = APIClient()
    if user is not None:
        token = str(SessionVersionRefreshToken.for_user(user).access_token)
    if token:
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def _maintenance(**fields) -> MaintenanceMode:
    return MaintenanceMode.objects.create(enabled=True, message_en="Maintenance", retry_after_seconds=30, **fields)


@pytest.mark.parametrize(
    ("bypass_staff", "allowed_roles", "role", "blocked"),
    [
        (True, [], UserRole.ADMIN, False),
        (True, [], UserRole.IT_SUPPORT, False),
        (True, [], UserRole.PRINT_STAFF, False),
        (True, [], UserRole.STUDENT, True),
        (True, [], UserRole.NORMAL_USER, True),
        (False, [], UserRole.ADMIN, True),
        (False, [], UserRole.STUDENT, True),
        (False, ["student"], UserRole.STUDENT, False),
        (False, ["student"], UserRole.NORMAL_USER, True),
        (False, ["student"], UserRole.ADMIN, True),
        (True, ["student"], UserRole.STUDENT, False),
        (True, ["student"], UserRole.NORMAL_USER, True),
        (True, ["student"], UserRole.ADMIN, False),
        (False, ["normal_user", "admin"], UserRole.NORMAL_USER, False),
        (False, ["normal_user", "admin"], UserRole.ADMIN, False),
    ],
)
def test_role_bypass_matrix(bypass_staff, allowed_roles, role, blocked):
    _maintenance(bypass_staff=bypass_staff, allowed_roles=allowed_roles)
    response = _client(_user(role, 1)).get(PROTECTED)
    if blocked:
        assert response.status_code == 503
        assert response.json()["code"] == "MAINTENANCE_MODE"
        assert response["Retry-After"] == "30"
    else:
        assert response.status_code != 503


@pytest.mark.parametrize("bypass_staff", [True, False])
def test_anonymous_and_bad_tokens_never_bypass(bypass_staff):
    _maintenance(bypass_staff=bypass_staff, allowed_roles=["student", "admin"])
    assert _client().get(PROTECTED).status_code == 503
    assert _client(token="not-a-jwt").get(PROTECTED).status_code == 503
    # A refresh token is not an access token.
    refresh = str(SessionVersionRefreshToken.for_user(_user(UserRole.ADMIN, 2)))
    assert _client(token=refresh).get(PROTECTED).status_code == 503


def test_revoked_or_inactive_users_do_not_bypass():
    _maintenance()
    admin = _user(UserRole.ADMIN, 3)
    client = _client(admin)
    assert client.get(PROTECTED).status_code != 503
    admin.invalidate_sessions()  # role/status/password events advance the session version
    from django.core.cache import cache

    cache.clear()
    assert client.get(PROTECTED).status_code == 503

    other = _user(UserRole.ADMIN, 4)
    other_client = _client(other)
    User.objects.filter(pk=other.pk).update(is_active=False)
    assert other_client.get(PROTECTED).status_code == 503


def test_exempt_paths_stay_reachable_for_everyone():
    _maintenance(bypass_staff=False, allowed_roles=[])
    client = _client()
    assert client.get("/api/v1/health/live/").status_code == 200
    assert client.get("/api/v1/policies/current/").status_code == 200
    login = client.post("/api/v1/auth/login/", {"identifier": "x@example.test", "password": "nope"}, format="json")
    assert login.status_code == 400  # reached the view, not the maintenance gate
    assert client.get("/api/v1/mobile/bootstrap/", HTTP_X_APP_PLATFORM="android").status_code == 200


def test_role_lookup_is_cached_so_repeat_requests_add_no_user_query():
    _maintenance()
    client = _client(_user(UserRole.ADMIN, 5))
    client.get(PROTECTED)  # warms the maintenance + role caches
    with CaptureQueriesContext(connection) as queries:
        assert client.get(PROTECTED).status_code != 503
    role_lookups = [
        q for q in queries.captured_queries if '"accounts_user"' in q["sql"] and "session_version" in q["sql"]
    ]
    # Only the authenticated view's own user load may appear; the middleware's role lookup is served from cache.
    assert len(role_lookups) <= 1


def test_no_token_work_when_nobody_can_bypass():
    _maintenance(bypass_staff=False, allowed_roles=[])
    client = _client(_user(UserRole.ADMIN, 6))
    with CaptureQueriesContext(connection) as queries:
        assert client.get(PROTECTED).status_code == 503
    assert not any('"accounts_user"' in q["sql"] for q in queries.captured_queries)


def test_staff_bypass_does_not_skip_the_mandatory_update_gate():
    _maintenance()
    MobileAppReleasePolicy.objects.create(
        platform="android", latest_version="2.0.0", latest_build=20, minimum_supported_build=10, update_mode="required"
    )
    response = _client(_user(UserRole.ADMIN, 7)).get(PROTECTED, HTTP_X_APP_PLATFORM="android", HTTP_X_APP_BUILD="1")
    assert response.status_code == 426


def test_maintenance_serializer_rejects_unknown_allowed_roles():
    admin = _user(UserRole.ADMIN, 8)
    client = _client(admin)
    bad = client.post(
        "/api/v1/dashboard/maintenance-modes/", {"enabled": False, "allowed_roles": ["emperor"]}, format="json"
    )
    assert bad.status_code == 400
    good = client.post(
        "/api/v1/dashboard/maintenance-modes/",
        {"enabled": False, "allowed_roles": ["student", "student"]},
        format="json",
    )
    assert good.status_code == 201
    assert good.json()["data"]["allowed_roles"] == ["student"]


def test_policy_status_tells_the_client_when_reacceptance_is_required():
    user = _user(UserRole.STUDENT, 9)
    client = _client(user)
    anonymous = APIClient().get("/api/v1/policies/status/")
    assert anonymous.status_code == 401

    TermsVersion.objects.create(version="t1", requires_reacceptance=False)
    PrivacyPolicyVersion.objects.create(version="p1", requires_reacceptance=True)
    data = client.get("/api/v1/policies/status/").json()["data"]
    assert data["terms"]["must_accept"] is False  # not flagged: acceptance is optional
    assert data["privacy"]["must_accept"] is True and data["privacy"]["accepted"] is False
    assert data["must_accept_any"] is True

    accepted = client.post("/api/v1/policies/accept/", {"privacy_version": "p1", "locale": "ar"}, format="json")
    assert accepted.status_code == 200
    data = client.get("/api/v1/policies/status/").json()["data"]
    assert data["privacy"]["accepted"] is True and data["privacy"]["accepted_version"] == "p1"
    assert data["must_accept_any"] is False

    # Publishing a new version with the flag re-opens the gate for the same user.
    PrivacyPolicyVersion.objects.create(version="p2", requires_reacceptance=True)
    data = client.get("/api/v1/policies/status/").json()["data"]
    assert data["privacy"]["version"] == "p2" and data["privacy"]["must_accept"] is True
    assert data["must_accept_any"] is True
