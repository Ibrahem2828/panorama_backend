"""Enrollment-only joining and the dashboard join-requests screen (list, summary, bulk decision)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.audit.models import AuditAction, AuditLog
from apps.enrollment.models import EnrollmentItem, EnrollmentRequest
from apps.enrollment.services import EnrollmentService
from apps.groups.models import Group, GroupMembership, GroupMembershipStatus
from apps.groups.services import ENROLLMENT_ONLY_MESSAGE, GroupJoinEnrollmentOnly, GroupMembershipService
from apps.notifications.models import Notification
from apps.universities.models import AcademicYear, Faculty, Semester, Subject, University

pytestmark = pytest.mark.django_db

JOIN_URL = "/api/v1/groups/{}/join/"
LIST_URL = "/api/v1/dashboard/group-join-requests/"
APPROVED_TITLE = "تم قبول طلب الانضمام"
UPDATED_TITLE = "تم تحديث عضوية الغروب"


def _client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


def _user(email: str, role: str, n: int, name: str | None = None) -> User:
    return User.objects.create_user(
        full_name=name or email.split("@")[0],
        email=email,
        phone_number=f"+9639910{n:05d}",
        password="StrongPass123!",
        role=role,
    )


def _student(email, n, world, *, verified=True, number="") -> User:
    user = _user(email, UserRole.STUDENT, n, name=f"Student {n}")
    StudentProfile.objects.create(
        user=user,
        university=world["uni"],
        faculty=world["faculty"],
        academic_year=world["year"],
        semester=world["sem"],
        student_number=number or f"21500{n:02d}",
        verification_status=StudentVerificationStatus.APPROVED if verified else StudentVerificationStatus.PENDING,
    )
    return user


@pytest.fixture
def world():
    uni = University.objects.create(name="Uni", code="U")
    faculty = Faculty.objects.create(university=uni, name="Dentistry", code="DEN")
    faculty2 = Faculty.objects.create(university=uni, name="Law", code="LAW")
    year = AcademicYear.objects.create(name="Year 1", order=1)
    year2 = AcademicYear.objects.create(name="Year 2", order=2)
    sem = Semester.objects.create(name="Semester 1", order=1)
    sem2 = Semester.objects.create(name="Semester 2", order=2)
    admin = _user("admin@t.test", UserRole.ADMIN, 1, "Admin")
    support = _user("support@t.test", UserRole.PRINT_STAFF, 2, "Print")
    w: dict[str, Any] = {
        "uni": uni,
        "faculty": faculty,
        "faculty2": faculty2,
        "year": year,
        "year2": year2,
        "sem": sem,
        "sem2": sem2,
    }
    w["admin"], w["support"] = admin, support
    w["student"] = _student("s1@t.test", 10, w)
    w["subject"] = Subject.objects.create(faculty=faculty, academic_year=year, semester=sem, name="Anatomy", code="A1")
    w["subject2"] = Subject.objects.create(
        faculty=faculty2, academic_year=year2, semester=sem2, name="Civil Law", code="L1"
    )
    w["subject_group"] = Group.objects.create(
        name="Anatomy group", university=uni, faculty=faculty, subject=w["subject"], created_by=admin
    )
    w["subject_group2"] = Group.objects.create(
        name="Civil group", university=uni, faculty=faculty2, subject=w["subject2"], created_by=admin
    )
    w["faculty_group"] = Group.objects.create(
        name="Dentistry community", university=uni, faculty=faculty, created_by=admin
    )
    w["general_group"] = Group.objects.create(name="General", university=uni, created_by=admin)
    return w


def _pending(user, group) -> GroupMembership:
    return GroupMembership.objects.create(group=group, user=user, status=GroupMembershipStatus.PENDING)


# -- enrollment-only joining ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("group_key", ["subject_group", "faculty_group", "general_group"])
@pytest.mark.parametrize("verified", [True, False])
def test_join_is_refused_for_every_kind_of_group_and_student(world, group_key, verified):
    student = world["student"] if verified else _student("unv@t.test", 11, world, verified=False)
    group = world[group_key]
    response = _client(student).post(JOIN_URL.format(group.pk))
    assert response.status_code == 403
    assert response.data["code"] == "GROUP_JOIN_ENROLLMENT_ONLY"
    assert response.data["message"] == ENROLLMENT_ONLY_MESSAGE
    assert not GroupMembership.objects.filter(group=group, user=student).exists()


def test_join_is_refused_even_for_unknown_group_and_leaves_pending_rows_alone(world):
    existing = _pending(world["student"], world["faculty_group"])
    assert _client(world["student"]).post(JOIN_URL.format(999999)).data["code"] == "GROUP_JOIN_ENROLLMENT_ONLY"
    assert _client(world["student"]).post(JOIN_URL.format(world["faculty_group"].pk)).status_code == 403
    existing.refresh_from_db()
    assert existing.status == GroupMembershipStatus.PENDING


def test_join_route_still_authenticates_and_rejects_non_students(world):
    assert APIClient().post(JOIN_URL.format(world["faculty_group"].pk)).status_code == 401
    response = _client(world["admin"]).post(JOIN_URL.format(world["faculty_group"].pk))
    assert response.status_code == 403 and response.data["code"] == "PERMISSION_DENIED"


def test_service_join_refuses_student_initiated_joins_but_staff_can_add(world):
    student, group = world["student"], world["faculty_group"]
    with pytest.raises(GroupJoinEnrollmentOnly):
        GroupMembershipService.join(student, group)
    with pytest.raises(GroupJoinEnrollmentOnly):
        GroupMembershipService.join(student, group, initiated_by=student)
    membership = GroupMembershipService.join(student, group, initiated_by=world["admin"])
    assert membership.status == GroupMembershipStatus.PENDING


def test_available_groups_list_stays_readable_and_never_advertises_joining(world):
    response = _client(world["student"]).get("/api/v1/groups/available/")
    assert response.status_code == 200
    rows = response.data["data"]["results"]
    assert rows and all(row["can_request_join"] is False for row in rows)


def test_enrollment_approval_still_joins_groups_and_reactivates_term_ended_members(world):
    student = world["student"]
    GroupMembership.objects.create(
        group=world["subject_group"],
        user=student,
        status=GroupMembershipStatus.TERM_ENDED,
        ended_at=timezone.now(),
    )
    enrollment = EnrollmentRequest.objects.create(
        user=student,
        student_profile=student.student_profile,
        faculty=world["faculty"],
        academic_year=world["year"],
        semester=world["sem"],
        screenshot="x.png",
    )
    EnrollmentItem.objects.create(request=enrollment, subject=world["subject"])
    EnrollmentService.decide(enrollment, world["admin"], approve_item_ids=None)
    membership = GroupMembership.objects.get(group=world["subject_group"], user=student)
    assert membership.status == GroupMembershipStatus.APPROVED and membership.ended_at is None


def test_faculty_community_backfill_still_works(world):
    call_command("backfill_faculty_communities")
    assert GroupMembership.objects.filter(
        group=world["faculty_group"], user=world["student"], status=GroupMembershipStatus.APPROVED
    ).exists()


# -- global list ---------------------------------------------------------------------------------------------------


@pytest.fixture
def requests_world(world):
    s1 = world["student"]
    s2 = _student("s2@t.test", 12, world, number="9990002")
    s3 = _student("s3@t.test", 13, world)
    base = timezone.now() - timedelta(days=5)
    rows = {
        "a": _pending(s1, world["subject_group"]),
        "b": _pending(s2, world["subject_group"]),
        "c": _pending(s1, world["faculty_group"]),
        "d": _pending(s3, world["general_group"]),
        "e": _pending(s2, world["subject_group2"]),
    }
    for index, key in enumerate("abcde"):
        GroupMembership.objects.filter(pk=rows[key].pk).update(created_at=base + timedelta(days=index))
    rows["done"] = GroupMembership.objects.create(
        group=world["faculty_group"],
        user=s3,
        status=GroupMembershipStatus.APPROVED,
        reviewed_by=world["admin"],
        reviewed_at=timezone.now(),
    )
    world["rows"], world["s2"], world["s3"] = rows, s2, s3
    return world


def _ids(response):
    assert response.status_code == 200, response.data
    return [row["id"] for row in response.data["data"]["results"]]


def test_list_defaults_to_pending_newest_first_with_full_row_shape(requests_world):
    w = requests_world
    response = _client(w["admin"]).get(LIST_URL)
    rows = response.data["data"]["results"]
    assert [r["id"] for r in rows] == [w["rows"][k].pk for k in "edcba"]
    row = next(r for r in rows if r["id"] == w["rows"]["a"].pk)
    assert row["status"] == "pending" and row["reviewed_by"] is None and row["reviewed_at"] is None
    assert row["note"] is None
    assert row["user"] == {
        "id": w["student"].pk,
        "full_name": "Student 10",
        "email": "s1@t.test",
        "student_number": "2150010",
        "verification_status": "approved",
    }
    assert row["group"] == {
        "id": w["subject_group"].pk,
        "name": "Anatomy group",
        "kind": "subject",
        "subject": {"id": w["subject"].pk, "name": "Anatomy"},
        "faculty": {"id": w["faculty"].pk, "name": "Dentistry"},
        "academic_year": {"id": w["year"].pk, "name": "Year 1"},
        "semester": {"id": w["sem"].pk, "name": "Semester 1"},
    }
    general = next(r for r in rows if r["id"] == w["rows"]["d"].pk)["group"]
    assert general["kind"] == "general" and general["subject"] is None and general["faculty"] is None


def _day(n: int) -> str:
    return (timezone.now() - timedelta(days=n)).date().isoformat()


def test_list_filters(requests_world):
    w, client, r = requests_world, _client(requests_world["admin"]), requests_world["rows"]
    assert _ids(client.get(LIST_URL, {"status": "approved"})) == [r["done"].pk]
    assert _ids(client.get(LIST_URL, {"ordering": "created_at"})) == [r[k].pk for k in "abcde"]
    assert set(_ids(client.get(LIST_URL, {"group": w["subject_group"].pk}))) == {r["a"].pk, r["b"].pk}
    assert set(_ids(client.get(LIST_URL, {"subject": w["subject"].pk}))) == {r["a"].pk, r["b"].pk}
    assert set(_ids(client.get(LIST_URL, {"faculty": w["faculty2"].pk}))) == {r["e"].pk}
    assert set(_ids(client.get(LIST_URL, {"academic_year": w["year2"].pk}))) == {r["e"].pk}
    assert set(_ids(client.get(LIST_URL, {"semester": w["sem2"].pk}))) == {r["e"].pk}
    assert set(_ids(client.get(LIST_URL, {"kind": "subject"}))) == {r["a"].pk, r["b"].pk, r["e"].pk}
    assert set(_ids(client.get(LIST_URL, {"kind": "faculty"}))) == {r["c"].pk}
    assert set(_ids(client.get(LIST_URL, {"kind": "general"}))) == {r["d"].pk}
    assert set(_ids(client.get(LIST_URL, {"search": "9990002"}))) == {r["b"].pk, r["e"].pk}
    assert set(_ids(client.get(LIST_URL, {"search": "s3@t.test"}))) == {r["d"].pk}
    assert set(_ids(client.get(LIST_URL, {"search": "Student 10"}))) == {r["a"].pk, r["c"].pk}
    assert set(_ids(client.get(LIST_URL, {"search": "general"}))) == {r["d"].pk}
    assert set(_ids(client.get(LIST_URL, {"created_after": _day(2)}))) == {r["d"].pk, r["e"].pk}
    assert set(_ids(client.get(LIST_URL, {"created_before": _day(4)}))) == {r["a"].pk, r["b"].pk}


@pytest.mark.parametrize(
    "params",
    [
        {"status": "term_ended"},
        {"status": "bogus"},
        {"kind": "x"},
        {"created_after": "2026-13-40"},
        {"created_before": "31/01/2026"},
        {"faculty": "abc"},
        {"ordering": "user"},
    ],
)
def test_list_rejects_invalid_filters(requests_world, params):
    assert _client(requests_world["admin"]).get(LIST_URL, params).status_code == 400


def test_list_query_count_is_constant(requests_world):
    w, client = requests_world, _client(requests_world["admin"])
    with CaptureQueriesContext(connection) as small:
        client.get(LIST_URL)
    for i in range(6):
        extra = _student(f"x{i}@t.test", 30 + i, w)
        _pending(extra, w["subject_group"])
    with CaptureQueriesContext(connection) as large:
        client.get(LIST_URL)
    assert len(large) == len(small)


def test_summary_counts_and_respects_filters_except_status(requests_world):
    w, client = requests_world, _client(requests_world["admin"])
    GroupMembership.objects.filter(pk=w["rows"]["done"].pk).update(reviewed_at=timezone.now() - timedelta(days=3))
    GroupMembership.objects.create(
        group=w["subject_group2"],
        user=w["s3"],
        status=GroupMembershipStatus.REJECTED,
        reviewed_at=timezone.now(),
    )
    GroupMembership.objects.create(
        group=w["subject_group"],
        user=_student("t@t.test", 40, w),
        status=GroupMembershipStatus.APPROVED,
        reviewed_at=timezone.now(),
    )
    data = client.get(LIST_URL + "summary/").data["data"]
    assert data == {"pending": 5, "approved_today": 1, "rejected_today": 1}
    scoped = client.get(LIST_URL + "summary/", {"faculty": w["faculty2"].pk, "status": "approved"}).data["data"]
    assert scoped == {"pending": 1, "approved_today": 0, "rejected_today": 1}
    assert client.get(LIST_URL + "summary/", {"kind": "nope"}).status_code == 400


def test_list_summary_and_bulk_need_groups_manage(requests_world):
    w = requests_world
    body = {"ids": [w["rows"]["a"].pk], "decision": "approve"}
    for user in (w["student"], w["support"]):
        client = _client(user)
        assert client.get(LIST_URL).status_code == 403
        assert client.get(LIST_URL + "summary/").status_code == 403
        assert client.post(LIST_URL + "bulk-decision/", body, format="json").status_code == 403
    anon = APIClient()
    assert anon.get(LIST_URL).status_code == 401
    assert GroupMembership.objects.get(pk=w["rows"]["a"].pk).status == GroupMembershipStatus.PENDING


# -- bulk decision -------------------------------------------------------------------------------------------------


def test_bulk_approve_reuses_the_single_action_path_with_mixed_results(requests_world):
    w, client, r = requests_world, _client(requests_world["admin"]), requests_world["rows"]
    body = {"ids": [r["a"].pk, r["done"].pk, 424242, r["b"].pk, r["a"].pk], "decision": "approve"}
    response = client.post(LIST_URL + "bulk-decision/", body, format="json")
    assert response.status_code == 200
    data = response.data["data"]
    assert data["results"] == [
        {"id": r["a"].pk, "ok": True},
        {"id": r["done"].pk, "ok": False, "error": "NOT_PENDING"},
        {"id": 424242, "ok": False, "error": "NOT_FOUND"},
        {"id": r["b"].pk, "ok": True},
    ]
    assert (data["succeeded"], data["failed"]) == (2, 2)
    for key in ("a", "b"):
        membership = GroupMembership.objects.get(pk=r[key].pk)
        assert membership.status == GroupMembershipStatus.APPROVED
        assert membership.reviewed_by_id == w["admin"].pk and membership.joined_at
    # notified exactly once each, audited like the single action
    assert Notification.objects.filter(user=w["student"], title=APPROVED_TITLE).count() == 1
    assert Notification.objects.filter(user=w["s2"], title=APPROVED_TITLE).count() == 1
    assert Notification.objects.filter(user=w["s3"], title=APPROVED_TITLE).count() == 0
    assert AuditLog.objects.filter(action=AuditAction.GROUP_MEMBERSHIP_APPROVED).count() == 2
    # a second run is all NOT_PENDING and notifies nobody again
    again = client.post(LIST_URL + "bulk-decision/", {"ids": [r["a"].pk], "decision": "approve"}, format="json")
    assert again.data["data"]["results"] == [{"id": r["a"].pk, "ok": False, "error": "NOT_PENDING"}]
    assert Notification.objects.filter(user=w["student"], title=APPROVED_TITLE).count() == 1


def test_bulk_reject_carries_the_note_and_one_failure_does_not_roll_back_others(requests_world, monkeypatch):
    w, client, r = requests_world, _client(requests_world["admin"]), requests_world["rows"]
    real = GroupMembershipService.review

    def flaky(membership, reviewer, status, request=None, **kwargs):
        if membership.pk == r["b"].pk:
            raise RuntimeError("boom")
        return real(membership, reviewer, status, request=request, **kwargs)

    monkeypatch.setattr(GroupMembershipService, "review", staticmethod(flaky))
    response = client.post(
        LIST_URL + "bulk-decision/",
        {"ids": [r["a"].pk, r["b"].pk, r["c"].pk], "decision": "reject", "note": "غير مؤهل"},
        format="json",
    )
    results = response.data["data"]["results"]
    assert [item["ok"] for item in results] == [True, False, True]
    assert GroupMembership.objects.get(pk=r["a"].pk).status == GroupMembershipStatus.REJECTED
    assert GroupMembership.objects.get(pk=r["b"].pk).status == GroupMembershipStatus.PENDING
    assert GroupMembership.objects.get(pk=r["c"].pk).status == GroupMembershipStatus.REJECTED
    note = Notification.objects.filter(user=w["student"], title=UPDATED_TITLE).first()
    assert note and "غير مؤهل" in note.body


@pytest.mark.parametrize(
    "body",
    [
        {"ids": [], "decision": "approve"},
        {"ids": list(range(1, 102)), "decision": "approve"},
        {"ids": [1], "decision": "block"},
        {"ids": [1], "decision": "approve", "note": "x" * 501},
        {"decision": "approve"},
    ],
)
def test_bulk_validates_the_body(requests_world, body):
    assert _client(requests_world["admin"]).post(LIST_URL + "bulk-decision/", body, format="json").status_code == 400
