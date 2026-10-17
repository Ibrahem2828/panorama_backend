from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.choices import UserRole
from apps.accounts.dashboard_serializers import ALL_CAPABILITIES
from apps.accounts.models import PermissionEffect, User, UserPermissionOverride
from apps.accounts.permissions import PermissionService


def make_user(role=UserRole.NORMAL_USER, email="batch@example.com"):
    return User.objects.create_user(
        full_name="Batch", email=email, phone_number="+963944440009", password="StrongPass123!", role=role
    )


@pytest.mark.django_db
@pytest.mark.parametrize("role", [UserRole.NORMAL_USER, UserRole.ADMIN, UserRole.PRINT_STAFF])
def test_batch_matches_the_one_by_one_answer_with_every_kind_of_override(role):
    user = make_user(role)
    codes = list(ALL_CAPABILITIES)
    now = timezone.now()
    # a deny, an expired deny that must be ignored, a plain allow (one override per code is enforced)
    UserPermissionOverride.objects.create(user=user, permission_code=codes[0], effect=PermissionEffect.DENY)
    UserPermissionOverride.objects.create(
        user=user, permission_code=codes[1], effect=PermissionEffect.DENY, expires_at=now - timedelta(minutes=1)
    )
    UserPermissionOverride.objects.create(user=user, permission_code=codes[2], effect=PermissionEffect.ALLOW)

    expected = {code for code in codes if PermissionService.has(user, code)}
    with CaptureQueriesContext(connection) as queries:
        batch = PermissionService.granted_among(user, codes)
    assert batch == expected
    assert len(queries) == 1


@pytest.mark.django_db
def test_me_endpoint_does_not_query_overrides_once_per_capability():
    user = make_user()
    client = APIClient()
    client.force_authenticate(user=user)
    client.get("/api/v1/auth/me/")
    with CaptureQueriesContext(connection) as queries:
        response = client.get("/api/v1/auth/me/")
    assert response.status_code == 200
    assert sum("userpermissionoverride" in q["sql"] for q in queries) <= 1
