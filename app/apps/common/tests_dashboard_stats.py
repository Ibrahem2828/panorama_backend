from __future__ import annotations

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from apps.accounts.choices import UserRole
from apps.accounts.models import User

URL = "/api/v1/dashboard/stats/"


def make_user(role, n):
    return User.objects.create_user(
        full_name=f"U{n}",
        email=f"stats{n}@example.test",
        phone_number=f"+96397700{n:04d}",
        password="StrongPass123!",
        role=role,
    )


def get_as(user):
    client = APIClient()
    client.force_authenticate(user)
    return client.get(URL)


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()


@pytest.mark.django_db
def test_admin_sees_every_section_and_soft_deleted_rows_are_excluded():
    admin = make_user(UserRole.ADMIN, 1)
    gone = make_user(UserRole.NORMAL_USER, 2)
    User.objects.filter(pk=gone.pk).update(is_deleted=True)
    response = get_as(admin)
    assert response.status_code == 200
    data = response.data["data"]
    assert set(data) == {"users", "printing", "groups", "files", "support", "feedback"}
    assert data["users"]["total"] == 1  # the soft-deleted account is not counted


@pytest.mark.django_db
def test_print_staff_only_gets_the_printing_section():
    staff = make_user(UserRole.PRINT_STAFF, 3)
    data = get_as(staff).data["data"]
    assert "total_orders" in data["printing"]
    assert data["users"] == data["groups"] == data["files"] == data["support"] == data["feedback"] == {}


@pytest.mark.django_db
def test_stats_cost_a_handful_of_queries_and_are_cached(django_assert_max_num_queries):
    admin = make_user(UserRole.ADMIN, 4)
    client = APIClient()
    client.force_authenticate(admin)
    with django_assert_max_num_queries(40):
        assert client.get(URL).status_code == 200
    # second call is served from cache: only capability lookups remain
    with django_assert_max_num_queries(25):
        assert client.get(URL).status_code == 200
