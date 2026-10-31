"""System activity log: recording, login failure reasons, the dashboard API, retention and the staff-activity extension."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts import lockout
from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.audit.activity import mask_identifier, normalise_route
from apps.audit.activity_registry import ROUTE_LABELS, SKIP_ROUTES, is_skipped, label_for
from apps.audit.models import (
    AuditAction,
    AuditLog,
    LoginFailureReason,
    SystemActivity,
    SystemActivityKind,
)

pytestmark = pytest.mark.django_db

PASSWORD = "StrongPass123!"
LOGIN = "/api/v1/auth/login/"
LIST = "/api/v1/dashboard/system-activity/"
SUMMARY = "/api/v1/dashboard/system-activity/summary/"
USERS = "/api/v1/dashboard/system-activity/users/"


def make(role=UserRole.NORMAL_USER, n=1, **extra) -> User:
    extra.setdefault("is_email_verified", True)
    return User.objects.create_user(
        full_name=f"Person {n}",
        email=f"person{n}@example.test",
        phone_number=f"+96397700{n:04d}",
        password=PASSWORD,
        role=role,
        **extra,
    )


def api(user=None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user)
    return client


def results(response) -> list[dict]:
    assert response.status_code == 200, response.content
    return response.json()["data"]["results"]


@pytest.fixture
def admin():
    return make(UserRole.ADMIN, 100)


def activity(user=None, *, kind="request", method="POST", route="dashboard/groups/", status=200, age_hours=0, **kw):
    row = SystemActivity.objects.create(
        user=user,
        user_label=getattr(user, "full_name", ""),
        role=getattr(user, "role", ""),
        kind=kind,
        method=method,
        route=route,
        status_code=status,
        action_label=kw.pop("action_label", "x"),
        **kw,
    )
    if age_hours:
        SystemActivity.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(hours=age_hours))
    return row


# ---------------------------------------------------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------------------------------------------------


def test_authenticated_write_is_recorded_with_pattern_label_and_object():
    staff = make(UserRole.ADMIN, 1)
    target = make(UserRole.NORMAL_USER, 2)
    response = api(staff).post(
        f"/api/v1/dashboard/users/{target.pk}/end-sessions/?token=SECRET&x=1",
        {"password": "hunter2-secret"},
        format="json",
        HTTP_USER_AGENT="U" * 500,
    )
    assert response.status_code == 200
    row = SystemActivity.objects.get()
    assert row.kind == SystemActivityKind.REQUEST
    assert (row.method, row.route, row.status_code) == ("POST", "dashboard/users/{user_id}/end-sessions/", 200)
    assert row.action_label == "إنهاء جلسات مستخدم"
    assert (row.object_type, row.object_id) == ("user", str(target.pk))
    assert row.user == staff and "Person 1" in row.user_label and row.role == UserRole.ADMIN
    assert len(row.user_agent) == 200 and row.duration_ms is not None and row.ip
    blob = repr((row.route, row.extra, row.user_label, row.object_id, row.action_label))
    assert "SECRET" not in blob and "hunter2" not in blob and "?" not in row.route
    assert str(target.pk) not in row.route


def test_failed_write_is_recorded_as_error_outcome():
    staff = make(UserRole.ADMIN, 1)
    response = api(staff).post("/api/v1/dashboard/users/999999/end-sessions/", {}, format="json")
    assert response.status_code == 404
    row = SystemActivity.objects.get()
    assert row.kind == SystemActivityKind.REQUEST and row.status_code == 404


def test_reads_are_not_recorded():
    staff = make(UserRole.ADMIN, 1)
    assert api(staff).get("/api/v1/dashboard/users/").status_code == 200
    assert api(staff).get("/api/v1/auth/me/").status_code == 200
    assert SystemActivity.objects.count() == 0


def test_anonymous_requests_are_not_recorded():
    api().post("/api/v1/dashboard/users/1/end-sessions/", {}, format="json")
    assert SystemActivity.objects.count() == 0


def test_denied_response_of_an_authenticated_user_is_recorded():
    student = make(UserRole.NORMAL_USER, 3)
    response = api(student).get("/api/v1/dashboard/users/")
    assert response.status_code == 403
    row = SystemActivity.objects.get()
    assert row.kind == SystemActivityKind.DENIED and row.method == "GET" and row.status_code == 403
    assert row.route == "dashboard/users/" and row.user == student


def test_skip_list_noise_is_never_recorded():
    student = make(UserRole.NORMAL_USER, 3)
    client = api(student)
    assert client.post("/api/v1/notifications/read-all/", {}, format="json").status_code == 200
    client.post("/api/v1/notifications/999999/read/", {}, format="json")
    client.post("/api/v1/groups/1/chat/receipts/", {}, format="json")
    assert SystemActivity.objects.count() == 0
    for noisy in (
        "notifications/read-all/",
        "notifications/{pk}/read/",
        "notifications/summary/",
        "groups/{group_id}/chat/receipts/",
        "auth/token/refresh/",
        "dashboard/quick-access/",
        "printing/agent/heartbeat/",
        "health/ready/",
    ):
        assert is_skipped(noisy), noisy
    assert not is_skipped("dashboard/users/invite/")


def test_every_labelled_route_exists_and_every_skip_route_is_real_or_reserved():
    from apps.common.tests_authz_matrix import ROUTES

    for method, route in ROUTE_LABELS:
        assert route in ROUTES, f"label for unknown route {route}"
        assert method in ROUTES[route].methods, f"{method} is not served by {route}"
    reserved = {
        "chat/presence/",
        "chat/heartbeat/",
        "groups/{group_id}/chat/presence/",
        "groups/{group_id}/chat/heartbeat/",
    }
    for route in SKIP_ROUTES - reserved:
        assert route in ROUTES, f"stale skip route {route}"


def test_label_registry_crud_action_and_fallback():
    assert label_for("POST", "dashboard/groups/") == "إضافة مجموعة"
    assert label_for("PATCH", "dashboard/groups/{pk}/") == "تعديل مجموعة"
    assert label_for("DELETE", "dashboard/groups/{pk}/") == "حذف مجموعة"
    assert label_for("POST", "dashboard/users/invite/") == "دعوة مستخدم جديد"
    assert label_for("POST", "something/new/{pk}/") == "طلب POST something/new/{pk}/"
    assert label_for("DELETE", "dashboard/groups/") == "طلب DELETE dashboard/groups/"


def test_route_normalisation():
    assert normalise_route("api/v1/^dashboard/audit-logs/(?P<pk>[^/.]+)/$") == "dashboard/audit-logs/{pk}/"
    assert (
        normalise_route("api/v1/dashboard/users/<int:user_id>/end-sessions/")
        == "dashboard/users/{user_id}/end-sessions/"
    )
    assert normalise_route("api/v1/auth/invitations/<str:token>/") == "auth/invitations/{token}/"


def test_recording_failure_never_breaks_the_request(monkeypatch):
    staff = make(UserRole.ADMIN, 1)
    target = make(UserRole.NORMAL_USER, 2)

    def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(SystemActivity.objects, "create", boom)
    response = api(staff).post(f"/api/v1/dashboard/users/{target.pk}/end-sessions/", {}, format="json")
    assert response.status_code == 200


def test_object_id_prefers_the_last_id_kwarg_and_never_a_token():
    student = make(UserRole.STUDENT, 4)
    api(student).post("/api/v1/groups/55/messages/77/report/", {}, format="json")
    row = SystemActivity.objects.filter(route="groups/{group_id}/messages/{message_id}/report/").first()
    assert row is not None and (row.object_type, row.object_id) == ("message", "77")
    api(student).get("/api/v1/auth/invitations/abcSECRETtoken/")  # read, public: nothing recorded
    assert not SystemActivity.objects.filter(object_id__icontains="SECRET").exists()


# ---------------------------------------------------------------------------------------------------------------------
# Login events and failure reasons
# ---------------------------------------------------------------------------------------------------------------------


def login(identifier, password, **headers):
    return api().post(LOGIN, {"identifier": identifier, "password": password}, format="json", **headers)


def last_failure() -> SystemActivity:
    return SystemActivity.objects.filter(kind=SystemActivityKind.LOGIN_FAILED).latest("id")


def test_login_success_and_logout_are_recorded():
    user = make(UserRole.ADMIN, 1, must_change_password=True)
    response = login(user.email, PASSWORD, REMOTE_ADDR="203.0.113.9", HTTP_USER_AGENT="Dash/1")
    assert response.status_code == 200
    row = SystemActivity.objects.get(kind=SystemActivityKind.LOGIN_SUCCESS)
    assert row.user == user and row.ip == "203.0.113.9" and row.user_agent == "Dash/1"
    assert row.extra == {"must_change_password": True} and row.route == "auth/login/"
    refresh = response.json()["data"]["refresh"]
    out = api(user).post("/api/v1/auth/logout/", {"refresh": refresh}, format="json")
    assert out.status_code == 200
    assert SystemActivity.objects.filter(kind=SystemActivityKind.LOGOUT, user=user).count() == 1
    # the generic request recorder skips both: exactly one row per event
    assert SystemActivity.objects.count() == 2
    assert refresh not in repr(list(SystemActivity.objects.values()))


def test_failure_reason_unknown_identifier_is_invalid_credentials_without_user():
    response = login("ghost@example.test", "whatever")
    assert response.status_code == 400
    row = last_failure()
    assert row.user is None
    assert row.extra["reason"] == LoginFailureReason.INVALID_CREDENTIALS
    assert row.extra["user_found"] is False
    assert row.extra["identifier_masked"] == "gh***@example.test"
    assert "ghost@example.test" not in repr(row.extra)


def test_failure_reason_wrong_password_links_the_user():
    user = make(UserRole.ADMIN, 1)
    assert login(user.email, "WrongPass999!").status_code == 400
    row = last_failure()
    assert row.user == user and row.extra["reason"] == "invalid_credentials" and row.extra["user_found"] is True
    assert "WrongPass" not in repr(row.extra)


def test_failure_reason_password_not_set_for_an_unaccepted_invitation():
    user = make(UserRole.ADMIN, 1)
    user.set_unusable_password()
    user.save()
    response = login(user.email, "TempPass123!x")
    assert response.status_code == 400
    row = last_failure()
    assert row.user == user and row.extra["reason"] == "password_not_set"
    assert row.extra["note"] == "لم تُقبل الدعوة بعد"


def test_failure_reason_inactive_account():
    user = make(UserRole.ADMIN, 1, is_active=False)
    assert login(user.email, PASSWORD).status_code == 400
    row = last_failure()
    assert row.user == user and row.extra["reason"] == "inactive_account"


def test_failure_reason_unverified_keeps_its_own_message():
    user = make(UserRole.NORMAL_USER, 1, is_email_verified=False, is_phone_verified=False)
    response = login(user.email, PASSWORD)
    assert response.status_code == 400
    assert last_failure().extra["reason"] == "unverified"


def test_failure_reason_locked(settings):
    settings.LOGIN_LOCKOUT_THRESHOLD = 2
    user = make(UserRole.ADMIN, 1)
    for _ in range(2):
        assert login(user.email, "WrongPass999!").status_code == 400
    blocked = login(user.email, PASSWORD)
    assert blocked.status_code == 429
    row = last_failure()
    assert row.user == user and row.extra["reason"] == "locked" and row.status_code == 429


def test_client_response_is_identical_whatever_the_reason():
    unusable = make(UserRole.ADMIN, 1)
    unusable.set_unusable_password()
    unusable.save()
    wrong = make(UserRole.ADMIN, 2)
    inactive = make(UserRole.ADMIN, 3, is_active=False)
    bodies = []
    for identifier in ("ghost@example.test", unusable.email, wrong.email, inactive.email):
        response = login(identifier, "WrongPass999!")
        assert response.status_code == 400
        body = response.json()
        body.pop("request_id", None)
        bodies.append(body)
    assert all(body == bodies[0] for body in bodies)


def test_every_failure_reason_in_the_closed_set_is_covered():
    assert set(LoginFailureReason.values) == {
        "invalid_credentials",
        "password_not_set",
        "inactive_account",
        "unverified",
        "locked",
    }


def test_identifier_masking():
    assert mask_identifier("asma@gmail.com") == "as***@gmail.com"
    assert mask_identifier("a@gmail.com") == "a***@gmail.com"
    assert mask_identifier("+963912345123") == "+9639***123"
    assert mask_identifier("1234") == "12***"
    assert mask_identifier("") == ""
    assert "912345" not in mask_identifier("+963912345123")


# ---------------------------------------------------------------------------------------------------------------------
# Pasted password whitespace
# ---------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("decoration", ["  {}", "{}  ", " {} \n", " {}", "{}​", "﻿{}"])
def test_pasted_whitespace_around_the_password_still_logs_in(decoration):
    user = make(UserRole.ADMIN, 1)
    response = login(user.email, decoration.format(PASSWORD))
    assert response.status_code == 200
    assert SystemActivity.objects.filter(kind=SystemActivityKind.LOGIN_SUCCESS, user=user).count() == 1


def test_a_password_that_really_has_edge_spaces_still_works_exactly():
    user = make(UserRole.ADMIN, 1)
    user.set_password(" spaced pass 123! ")
    user.save()
    assert login(user.email, " spaced pass 123! ").status_code == 200
    assert login(user.email, "spaced pass 123!").status_code == 400


def test_whitespace_retry_does_not_double_count_lockout(settings):
    settings.LOGIN_LOCKOUT_THRESHOLD = 3
    user = make(UserRole.ADMIN, 1)
    key = lockout._FAIL_PREFIX + lockout._digest(user.email)
    assert login(user.email, "  WrongPass999!  ").status_code == 400
    assert cache.get(key) == 1  # one request, one failure, even though two candidates were checked
    assert login(user.email, "WrongPass999!").status_code == 400
    assert cache.get(key) == 2
    assert SystemActivity.objects.filter(kind=SystemActivityKind.LOGIN_FAILED).count() == 2


# ---------------------------------------------------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------------------------------------------------


def test_list_row_shape_and_default_ordering(admin):
    student = make(UserRole.STUDENT, 2, job_title="")
    old = activity(student, route="a/", age_hours=5)
    new = activity(student, route="b/", ip="198.51.100.4", user_agent="UA", duration_ms=12, extra={"k": 1})
    rows = results(api(admin).get(LIST))
    assert [r["id"] for r in rows] == [new.pk, old.pk]
    assert set(rows[0]) == {
        "id", "created_at", "user", "user_label", "kind", "action_label", "method", "route", "status_code",
        "object_type", "object_id", "ip", "user_agent", "duration_ms", "extra",
    }  # fmt: skip
    assert rows[0]["user"] == {
        "id": student.pk,
        "full_name": student.full_name,
        "email": student.email,
        "role": "student",
        "job_title": "",
    }
    assert rows[0]["ip"] == "198.51.100.4" and rows[0]["extra"] == {"k": 1}
    asc = results(api(admin).get(LIST, {"ordering": "created_at"}))
    assert [r["id"] for r in asc] == [old.pk, new.pk]


def test_list_user_may_be_null_for_unknown_login_attempts(admin):
    activity(None, kind="login_failed", method="POST", route="auth/login/", status=400)
    assert results(api(admin).get(LIST))[0]["user"] is None


def test_list_filters(admin):
    a, b = make(UserRole.STUDENT, 2), make(UserRole.ADMIN, 3)
    activity(a, route="dashboard/groups/", status=200, method="POST")
    activity(a, route="dashboard/files/{pk}/", status=500, method="DELETE")
    activity(b, route="printing/orders/", status=403, kind="denied", method="POST")
    activity(b, kind="login_failed", route="auth/login/", status=400)
    client = api(admin)

    def ids(**params: Any):
        return {r["id"] for r in results(client.get(LIST, params))}

    assert len(ids()) == 4
    assert len(ids(user=a.pk)) == 2
    assert len(ids(role="admin")) == 2
    assert len(ids(kind="denied")) == 1
    assert len(ids(method="delete")) == 1
    assert len(ids(outcome="error")) == 3 and len(ids(outcome="success")) == 1
    assert len(ids(status_code=500)) == 1
    assert len(ids(route="FILES")) == 1
    assert len(ids(search=a.full_name)) == 2
    assert len(ids(search=b.email)) == 2
    assert len(ids(search="groups")) == 1
    assert client.get(LIST, {"kind": "nope"}).status_code == 400
    assert client.get(LIST, {"outcome": "maybe"}).status_code == 400


def test_date_filters_are_inclusive_and_validated(admin):
    inside = activity(make(UserRole.STUDENT, 2), age_hours=0)
    old = activity(make(UserRole.STUDENT, 3), age_hours=24 * 10)
    today = timezone.localdate().isoformat()
    ten_days = timezone.localtime(timezone.now() - timedelta(days=10)).date().isoformat()
    client = api(admin)
    assert {r["id"] for r in results(client.get(LIST, {"created_after": today}))} == {inside.pk}
    assert {r["id"] for r in results(client.get(LIST, {"created_before": ten_days}))} == {old.pk}
    both = results(client.get(LIST, {"created_after": ten_days, "created_before": today}))
    assert {r["id"] for r in both} == {inside.pk, old.pk}
    assert client.get(LIST, {"created_after": "31-12-2026"}).status_code == 400
    assert client.get(LIST, {"created_before": "garbage"}).status_code == 400


def test_list_is_paginated(admin):
    user = make(UserRole.STUDENT, 2)
    for _ in range(5):
        activity(user)
    data = api(admin).get(LIST, {"page_size": 2}).json()["data"]
    assert data["count"] == 5 and len(data["results"]) == 2 and data["total_pages"] == 3


def test_summary_counts_and_filters(admin):
    a, b = make(UserRole.STUDENT, 2), make(UserRole.ADMIN, 3)
    activity(a, status=200)
    activity(a, status=500)
    activity(b, kind="login_failed", route="auth/login/", status=400)
    activity(None, kind="login_failed", route="auth/login/", status=400)
    activity(b, kind="login_success", route="auth/login/", status=200)
    activity(b, kind="denied", status=403)
    data = api(admin).get(SUMMARY).json()["data"]
    assert data["total"] == 6 and data["errors"] == 4 and data["failed_logins"] == 2
    assert data["active_users"] == 2
    assert data["by_kind"] == {"request": 2, "login_success": 1, "login_failed": 2, "logout": 0, "denied": 1}
    assert set(data["window"]) == {"from", "to"} and data["window"]["from"]
    filtered = api(admin).get(SUMMARY, {"user": str(a.pk), "created_after": "2020-01-01"}).json()["data"]
    assert filtered["total"] == 2 and filtered["errors"] == 1 and filtered["window"]["from"] == "2020-01-01"
    empty = api(admin).get(SUMMARY, {"user": 999999}).json()["data"]
    assert empty["total"] == 0 and empty["window"] == {"from": None, "to": None}
    assert api(admin).get(SUMMARY, {"created_after": "bad"}).status_code == 400


def test_users_rollup_content_and_filters(admin):
    staff = make(UserRole.PRINT_STAFF, 2)
    student = make(UserRole.STUDENT, 3)
    make(UserRole.STUDENT, 4)  # no activity: absent
    activity(staff, action_label="old", ip="1.1.1.1", age_hours=30)
    activity(staff, action_label="newest", ip="2.2.2.2")
    activity(staff, kind="login_failed", route="auth/login/", status=400, age_hours=2)
    activity(student, action_label="s", ip="3.3.3.3", age_hours=24 * 3)
    activity(student, kind="login_success", route="auth/login/", age_hours=1)
    rows = results(api(admin).get(USERS))
    by_id = {r["user"]["id"]: r for r in rows}
    assert set(by_id) == {staff.pk, student.pk}
    s = by_id[staff.pk]
    assert (s["requests_24h"], s["requests_7d"], s["failed_logins_24h"]) == (1, 2, 1)
    assert s["last_action_label"] == "newest" and s["last_ip"] == "2.2.2.2"
    assert s["user"]["role"] == "print_staff" and "job_title" in s["user"] and s["last_activity_at"]
    t = by_id[student.pk]
    assert (t["requests_24h"], t["requests_7d"], t["failed_logins_24h"]) == (0, 1, 0)
    client = api(admin)
    assert [r["user"]["id"] for r in results(client.get(USERS, {"role": "student"}))] == [student.pk]
    assert [r["user"]["id"] for r in results(client.get(USERS, {"search": "Person 2"}))] == [staff.pk]
    by_name = results(client.get(USERS, {"ordering": "user"}))
    assert [r["user"]["full_name"] for r in by_name] == sorted(r["user"]["full_name"] for r in by_name)
    assert results(client.get(USERS, {"ordering": "-requests_7d"}))[0]["user"]["id"] == staff.pk
    assert client.get(USERS, {"ordering": "password"}).status_code == 400
    assert client.get(USERS, {"role": "wizard"}).status_code == 400


def test_users_rollup_has_no_n_plus_one(admin):
    def query_count() -> int:
        with CaptureQueriesContext(connection) as ctx:
            assert api(admin).get(USERS).status_code == 200
        return len(ctx)

    first = make(UserRole.STUDENT, 2)
    activity(first)
    small = query_count()
    for n in range(3, 18):
        activity(make(UserRole.STUDENT, n))
    assert query_count() == small


def test_list_has_no_n_plus_one(admin):
    def query_count() -> int:
        with CaptureQueriesContext(connection) as ctx:
            assert api(admin).get(LIST).status_code == 200
        return len(ctx)

    activity(make(UserRole.STUDENT, 2))
    small = query_count()
    for n in range(3, 15):
        activity(make(UserRole.STUDENT, n))
    assert query_count() == small


@pytest.mark.parametrize("role", [UserRole.NORMAL_USER, UserRole.STUDENT, UserRole.PRINT_STAFF, UserRole.SUPPORT_STAFF])
@pytest.mark.parametrize("url", [LIST, SUMMARY, USERS])
def test_roles_without_audit_view_are_denied(role, url):
    assert api(make(role, 9)).get(url).status_code == 403


def test_anonymous_gets_401_and_writes_are_not_allowed(admin):
    for url in (LIST, SUMMARY, USERS):
        assert api().get(url).status_code == 401
    assert api(admin).post(LIST, {}, format="json").status_code == 405
    assert api(admin).delete(LIST).status_code == 405


def test_admin_and_it_support_can_read():
    assert api(make(UserRole.IT_SUPPORT, 8)).get(LIST).status_code == 200


# ---------------------------------------------------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------------------------------------------------


def test_retention_purges_only_rows_older_than_the_setting(settings):
    settings.SYSTEM_ACTIVITY_RETENTION_DAYS = 90
    user = make(UserRole.STUDENT, 2)
    old = activity(user, age_hours=24 * 91)
    edge = activity(user, age_hours=24 * 89)
    fresh = activity(user)
    call_command("purge_expired_sensitive_data", "--dry-run")
    assert SystemActivity.objects.count() == 3
    call_command("purge_expired_sensitive_data")
    assert set(SystemActivity.objects.values_list("pk", flat=True)) == {edge.pk, fresh.pk}
    assert not SystemActivity.objects.filter(pk=old.pk).exists()


def test_retention_zero_keeps_everything(settings):
    settings.SYSTEM_ACTIVITY_RETENTION_DAYS = 0
    activity(make(UserRole.STUDENT, 2), age_hours=24 * 900)
    call_command("purge_expired_sensitive_data")
    assert SystemActivity.objects.count() == 1


def test_default_retention_is_ninety_days():
    from django.conf import settings

    assert settings.SYSTEM_ACTIVITY_RETENTION_DAYS == 90


def test_deleting_a_user_keeps_the_snapshot_label():
    user = make(UserRole.STUDENT, 2)
    row = activity(user)
    User.objects.filter(pk=user.pk).delete()
    row.refresh_from_db()
    assert row.user is None and row.user_label == "Person 2"


# ---------------------------------------------------------------------------------------------------------------------
# Staff activity dialog extension and audit-log readability
# ---------------------------------------------------------------------------------------------------------------------


def activity_url(user) -> str:
    return f"/api/v1/dashboard/users/{user.pk}/activity/"


def test_staff_activity_has_recent_logins_and_invitation_pending(admin):
    from apps.accounts.models import PermissionTemplate

    template = PermissionTemplate.objects.get(key="print_officer")
    response = api(admin).post(
        "/api/v1/dashboard/users/invite/",
        {
            "full_name": "New Officer",
            "email": "officer@example.test",
            "phone_number": "+963988800999",
            "template": template.pk,
            "mode": "link",
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    invited = User.objects.get(email="officer@example.test")
    assert login(invited.email, "Guess123456!").status_code == 400
    data = api(admin).get(activity_url(invited)).json()["data"]
    assert data["invitation"]["state"] == "pending" and data["invitation"]["mode"] == "link"
    assert data["invitation"]["last_sent_at"] and data["invitation"]["password_set"] is False
    assert data["recent_logins"][0]["kind"] == "login_failed"
    assert data["recent_logins"][0]["reason"] == "password_not_set"
    assert data["recent_logins"][0]["note"] == "لم تُقبل الدعوة بعد"
    # existing keys are untouched
    assert {"last_login", "active_sessions", "must_change_password", "invitation_pending", "recent_actions"} <= set(
        data
    )


def test_staff_activity_invitation_states_and_temporary_password_mode(admin):
    from apps.accounts.models import PermissionTemplate, UserInvitation

    template = PermissionTemplate.objects.get(key="print_officer")
    api(admin).post(
        "/api/v1/dashboard/users/invite/",
        {
            "full_name": "Temp",
            "email": "temp@example.test",
            "phone_number": "+963988800998",
            "template": template.pk,
            "mode": "temporary_password",
        },
        format="json",
    )
    temp = User.objects.get(email="temp@example.test")
    data = api(admin).get(activity_url(temp)).json()["data"]
    assert data["invitation"]["state"] == "none" and data["invitation"]["mode"] == "temporary_password"
    assert data["invitation"]["password_set"] is True and data["invitation"]["last_sent_at"]

    api(admin).post(
        "/api/v1/dashboard/users/invite/",
        {
            "full_name": "Link",
            "email": "link@example.test",
            "phone_number": "+963988800997",
            "template": template.pk,
            "mode": "link",
        },
        format="json",
    )
    linked = User.objects.get(email="link@example.test")
    invitation = UserInvitation.objects.get(user=linked)
    for field, value, expected in (
        ("expires_at", timezone.now() - timedelta(hours=1), "expired"),
        ("revoked_at", timezone.now(), "revoked"),
        ("accepted_at", timezone.now(), "accepted"),
    ):
        UserInvitation.objects.filter(pk=invitation.pk).update(
            expires_at=timezone.now() + timedelta(hours=5), revoked_at=None, accepted_at=None
        )
        UserInvitation.objects.filter(pk=invitation.pk).update(**{field: value})
        state = api(admin).get(activity_url(linked)).json()["data"]["invitation"]["state"]
        assert state == expected


def test_staff_activity_recent_logins_limited_to_ten_and_newest_first(admin):
    user = make(UserRole.PRINT_STAFF, 2)
    for i in range(12):
        activity(
            user,
            kind="login_failed",
            route="auth/login/",
            status=400,
            age_hours=i + 1,
            extra={"reason": "invalid_credentials"},
        )
    activity(user, kind="request", route="x/")  # not a login: excluded
    data = api(admin).get(activity_url(user)).json()["data"]
    assert len(data["recent_logins"]) == 10
    stamps = [r["created_at"] for r in data["recent_logins"]]
    assert stamps == sorted(stamps, reverse=True)


def test_audit_log_serializer_has_actor_detail_and_target_label(admin):
    target = make(UserRole.STUDENT, 2)
    AuditLog.objects.create(
        actor=admin, action=AuditAction.USER_STATUS_CHANGED, target_type="User", target_id=str(target.pk)
    )
    AuditLog.objects.create(actor=None, action=AuditAction.FILE_UPDATED, target_type="Mystery", target_id="5")
    AuditLog.objects.create(actor=admin, action=AuditAction.FILE_UPDATED)
    with CaptureQueriesContext(connection) as ctx:
        response = api(admin).get("/api/v1/dashboard/audit-logs/")
    rows = {r["action"]: r for r in results(response)}
    user_row = rows["user_status_changed"]
    assert user_row["actor"] == admin.pk and user_row["actor_name"] == admin.full_name
    assert user_row["actor_detail"] == {
        "id": admin.pk,
        "full_name": admin.full_name,
        "email": admin.email,
        "role": "admin",
    }
    assert user_row["target_label"] == f"User #{target.pk} · {target.full_name}"
    file_rows = [r for r in results(response) if r["action"] == "file_updated"]
    labels = sorted(r["target_label"] for r in file_rows)
    assert labels == ["", "Mystery #5"]
    assert [r["actor_detail"] for r in file_rows if r["actor"] is None] == [None]
    assert len(ctx) < 10  # one extra query per target type, not per row
