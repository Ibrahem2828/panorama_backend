"""``GET dashboard/students/`` and ``dashboard/students/summary/``: every filter, ordering, validation, permissions, N+1."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.choices import StudentVerificationStatus as VS
from apps.accounts.choices import UserRole
from apps.accounts.models import StudentProfile, User
from apps.enrollment.models import EnrollmentRequest, EnrollmentStatus
from apps.groups.models import Group, GroupMembership, GroupMembershipStatus
from apps.universities.models import AcademicYear, Faculty, Major, Semester, University

pytestmark = pytest.mark.django_db

URL = "/api/v1/dashboard/students/"
SUMMARY = "/api/v1/dashboard/students/summary/"


def _mk_user(n: int, role: str = UserRole.STUDENT, **extra) -> User:
    return User.objects.create_user(
        full_name=extra.pop("full_name", f"User {n}"),
        email=extra.pop("email", f"u{n}@students.test"),
        phone_number=extra.pop("phone_number", f"+9639550{n:04d}"),
        password="StrongPass123!",
        role=role,
        **extra,
    )


def _student(world, n: int, *, status=VS.APPROVED, number: str = "", faculty=None, semester=None, **extra) -> User:
    profile_extra = {
        key: extra.pop(key) for key in ("needs_term_confirmation", "major", "academic_year") if key in extra
    }
    user = _mk_user(n, **extra)
    StudentProfile.objects.create(
        user=user,
        university=world.uni,
        faculty=faculty or world.fac,
        academic_year=profile_extra.pop("academic_year", world.y1),
        semester=semester or world.s1,
        student_number=number,
        verification_status=status,
        **profile_extra,
    )
    return user


def _set_created(user: User, when) -> None:
    User.objects.filter(pk=user.pk).update(created_at=when)


@pytest.fixture
def world():
    w = SimpleNamespace()
    w.uni = University.objects.create(name="Private University", code="PU")
    w.fac = Faculty.objects.create(university=w.uni, name="Informatics", code="4")
    w.fac2 = Faculty.objects.create(university=w.uni, name="Pharmacy", code="3")
    w.major = Major.objects.create(faculty=w.fac, name="Software", code="SE")
    w.y1 = AcademicYear.objects.create(name="Year 1", order=1)
    w.y2 = AcademicYear.objects.create(name="Year 2", order=2)
    w.s1 = Semester.objects.create(name="Semester 1", order=1)
    w.s2 = Semester.objects.create(name="Semester 2", order=2)
    w.admin = _mk_user(900, UserRole.ADMIN, full_name="Admin Boss", email="admin@students.test")
    w.group = Group.objects.create(name="G1", university=w.uni, created_by=w.admin)
    w.group2 = Group.objects.create(name="G2", university=w.uni, created_by=w.admin)

    now = timezone.now()
    w.ali = _student(w, 1, full_name="Ali Hassan", number="2150001", email="ali@students.test")
    w.sara = _student(
        w,
        2,
        full_name="Sara Omar",
        number="2150002",
        status=VS.PENDING,
        faculty=w.fac2,
        semester=w.s2,
        academic_year=w.y2,
    )
    w.omar = _student(
        w, 3, full_name="Omar Khaled", number="2150003", status=VS.REJECTED, is_active=False, major=w.major
    )
    w.lina = _student(
        w, 4, full_name="Lina Saleh", number="2150004", status=VS.INCOMPLETE, needs_term_confirmation=True
    )
    w.noor = _student(w, 5, full_name="Noor Adel", number="", status=VS.NEEDS_UPDATE)
    w.normal = _student(
        w, 6, full_name="Normal Person", number="2150006", status=VS.SUSPENDED, role=UserRole.NORMAL_USER
    )
    # not students: staff, student role without a profile, soft-deleted student
    w.staff = _mk_user(901, UserRole.ADMIN, full_name="Staff Member", email="staff@students.test")
    w.no_profile = _mk_user(902, UserRole.STUDENT, full_name="No Profile", email="np@students.test")
    w.deleted = _student(w, 7, full_name="Gone Student", number="2150007")
    User.objects.filter(pk=w.deleted.pk).update(is_deleted=True)

    GroupMembership.objects.create(group=w.group, user=w.ali, status=GroupMembershipStatus.APPROVED)
    GroupMembership.objects.create(group=w.group2, user=w.ali, status=GroupMembershipStatus.APPROVED)
    GroupMembership.objects.create(group=w.group, user=w.sara, status=GroupMembershipStatus.PENDING)
    GroupMembership.objects.create(group=w.group, user=w.omar, status=GroupMembershipStatus.APPROVED)
    GroupMembership.objects.create(group=w.group2, user=w.lina, status=GroupMembershipStatus.LEFT)

    EnrollmentRequest.objects.create(
        user=w.sara, student_profile=w.sara.student_profile, faculty=w.fac2, screenshot="e/1.png"
    )
    EnrollmentRequest.objects.create(
        user=w.ali,
        student_profile=w.ali.student_profile,
        faculty=w.fac,
        screenshot="e/2.png",
        status=EnrollmentStatus.APPROVED,
    )

    User.objects.filter(pk=w.ali.pk).update(last_login=now - timedelta(days=2))
    User.objects.filter(pk=w.sara.pk).update(last_login=now - timedelta(days=40))
    User.objects.filter(pk=w.omar.pk).update(last_login=now - timedelta(days=400))
    # lina / noor / normal never logged in

    for user, days in ((w.ali, 1), (w.sara, 10), (w.omar, 20), (w.lina, 30), (w.noor, 40), (w.normal, 50)):
        _set_created(user, now - timedelta(days=days))
    w.now = now
    return w


@pytest.fixture
def client(world):
    c = APIClient()
    c.force_authenticate(world.admin)
    return c


def _ids(response) -> list[int]:
    assert response.status_code == 200, response.content
    return [row["id"] for row in response.data["data"]["results"]]


def _names(response) -> list[str]:
    assert response.status_code == 200, response.content
    return [row["full_name"] for row in response.data["data"]["results"]]


def test_lists_only_students_with_profiles_and_envelope(client, world):
    response = client.get(URL)
    assert response.status_code == 200
    assert response.data["success"] is True
    assert response.data["data"]["count"] == 6
    ids = set(_ids(response))
    assert ids == {u.pk for u in (world.ali, world.sara, world.omar, world.lina, world.noor, world.normal)}
    assert world.staff.pk not in ids and world.no_profile.pk not in ids and world.deleted.pk not in ids


def test_row_shape(client, world):
    row = next(r for r in client.get(URL).data["data"]["results"] if r["id"] == world.ali.pk)
    assert set(row) == {
        "id",
        "full_name",
        "email",
        "phone_number",
        "student_number",
        "verification_status",
        "is_verified",
        "university",
        "faculty",
        "major",
        "academic_year",
        "semester",
        "is_active",
        "needs_term_confirmation",
        "last_login",
        "created_at",
        "groups_count",
        "pending_enrollments",
    }
    assert row["student_number"] == "2150001"
    assert row["verification_status"] == "approved" and row["is_verified"] is True
    assert row["university"] == {"id": world.uni.pk, "name": "Private University"}
    assert row["faculty"] == {"id": world.fac.pk, "name": "Informatics"}
    assert row["major"] is None
    assert row["academic_year"]["name"] == "Year 1" and row["semester"]["name"] == "Semester 1"
    assert row["groups_count"] == 2 and row["pending_enrollments"] == 0
    sara = next(r for r in client.get(URL).data["data"]["results"] if r["id"] == world.sara.pk)
    assert sara["is_verified"] is False
    assert sara["groups_count"] == 0  # only a pending membership
    assert sara["pending_enrollments"] == 1
    assert "job_title" not in row


def test_default_ordering_newest_first(client, world):
    assert _ids(client.get(URL))[0] == world.ali.pk


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("search=ali", ["ali"]),
        ("search=Sara%20Omar", ["sara"]),
        ("search=2150003", ["omar"]),
        ("search=u2@students", ["sara"]),
        ("search=%2B96395500", None),  # phone prefix: all students
        ("verification_status=pending", ["sara"]),
        ("verification_status=pending,rejected", ["sara", "omar"]),
        ("verification_status=approved", ["ali"]),
        ("is_verified=true", ["ali"]),
        ("is_verified=false", ["sara", "omar", "lina", "noor", "normal"]),
        ("faculty=FAC2", ["sara"]),
        ("major=MAJOR", ["omar"]),
        ("academic_year=Y2", ["sara"]),
        ("semester=S2", ["sara"]),
        ("is_active=false", ["omar"]),
        ("is_active=true", ["ali", "sara", "lina", "noor", "normal"]),
        ("needs_term_confirmation=true", ["lina"]),
        ("has_pending_enrollment=true", ["sara"]),
        ("has_pending_enrollment=false", ["ali", "omar", "lina", "noor", "normal"]),
        ("group=GROUP", ["ali", "omar"]),
        ("group=GROUP2", ["ali"]),
        ("no_groups=true", ["sara", "lina", "noor", "normal"]),
        ("no_groups=false", ["ali", "omar"]),
        ("registered_after=D15", ["ali", "sara"]),
        ("registered_before=D25", ["lina", "noor", "normal"]),
        ("inactive_days=30", ["sara", "omar", "lina", "noor", "normal"]),
        ("inactive_days=1", ["ali", "sara", "omar", "lina", "noor", "normal"]),
        ("inactive_days=500", ["lina", "noor", "normal"]),
        ("faculty=FAC2&verification_status=pending", ["sara"]),
        ("faculty=FAC2&verification_status=approved", []),
        ("no_groups=true&is_active=true&inactive_days=30", ["sara", "lina", "noor", "normal"]),
    ],
)
def test_filters(client, world, query, expected):
    def date_days_ago(days):
        return (world.now - timedelta(days=days)).date().isoformat()

    substitutions = {
        "FAC2": world.fac2.pk,
        "MAJOR": world.major.pk,
        "Y2": world.y2.pk,
        "S2": world.s2.pk,
        "GROUP2": world.group2.pk,
        "GROUP": world.group.pk,
        "D15": date_days_ago(15),
        "D25": date_days_ago(25),
    }
    for key in sorted(substitutions, key=len, reverse=True):
        query = query.replace(key, str(substitutions[key]))
    response = client.get(f"{URL}?{query}&ordering=student_number,full_name&page_size=100")
    got = {name.split()[0].lower() for name in _names(response)}
    if expected is None:
        assert len(got) == 6
    else:
        assert got == set(expected)


@pytest.mark.parametrize(
    ("ordering", "first"),
    [
        ("full_name", "Ali Hassan"),
        ("-full_name", "Sara Omar"),
        ("created_at", "Normal Person"),
        ("-created_at", "Ali Hassan"),
        ("last_login", "Omar Khaled"),
        ("-last_login", "Ali Hassan"),
    ],
)
def test_ordering(client, ordering, first, world):
    names = _names(client.get(f"{URL}?ordering={ordering}"))
    assert names[0] == first
    if "last_login" in ordering:  # never-logged-in students always sort last, in both directions
        assert set(names[-3:]) == {"Lina Saleh", "Noor Adel", "Normal Person"}


def test_page_size_and_pagination(client, world):
    data = client.get(f"{URL}?page_size=2&ordering=full_name").data["data"]
    assert data["count"] == 6 and data["total_pages"] == 3 and len(data["results"]) == 2
    assert data["next"] is not None


@pytest.mark.parametrize(
    "query",
    [
        "verification_status=bogus",
        "verification_status=approved,bogus",
        "is_verified=maybe",
        "is_active=2",
        "faculty=abc",
        "group=x",
        "registered_after=2026-13-45",
        "registered_after=15/01/2026",
        "registered_before=yesterday",
        "inactive_days=-1",
        "inactive_days=abc",
        "ordering=password",
        "ordering=-email",
        "no_groups=perhaps",
        "needs_term_confirmation=zzz",
        "has_pending_enrollment=nope",
    ],
)
def test_invalid_input_is_400(client, query):
    response = client.get(f"{URL}?{query}")
    assert response.status_code == 400, (query, response.content)


def test_date_bounds_are_inclusive(client, world):
    world.ali.refresh_from_db()
    created = timezone.localtime(world.ali.created_at).date().isoformat()
    both = client.get(f"{URL}?registered_after={created}&registered_before={created}")
    assert _ids(both) == [world.ali.pk]
    day_after = (timezone.localtime(world.ali.created_at).date() + timedelta(days=1)).isoformat()
    assert _ids(client.get(f"{URL}?registered_after={day_after}")) == []


def test_unknown_ids_yield_empty_not_error(client):
    assert _ids(client.get(f"{URL}?faculty=999999&group=999999")) == []


def test_summary_counts_and_filters(client, world):
    data = client.get(SUMMARY).data["data"]
    assert data["total"] == 6
    assert data["by_verification_status"] == {
        "incomplete": 1,
        "pending": 1,
        "approved": 1,
        "rejected": 1,
        "needs_update": 1,
        "suspended": 1,
    }
    scoped = client.get(f"{SUMMARY}?faculty={world.fac.pk}").data["data"]
    assert scoped["total"] == 5 and scoped["by_verification_status"]["pending"] == 0
    # verification_status is ignored (the segments are the point), other filters apply
    ignored = client.get(f"{SUMMARY}?verification_status=approved&no_groups=true").data["data"]
    assert ignored["total"] == 4 and ignored["by_verification_status"]["approved"] == 0
    assert ignored["by_verification_status"]["pending"] == 1
    empty = client.get(f"{SUMMARY}?faculty=999999").data["data"]
    assert empty["total"] == 0 and set(empty["by_verification_status"]) == set(VS.values)


def test_summary_invalid_filter_is_400(client):
    assert client.get(f"{SUMMARY}?is_active=maybe").status_code == 400
    assert client.get(f"{SUMMARY}?registered_after=nope").status_code == 400


def test_student_number_ordering(client, world):
    ascending = _names(client.get(f"{URL}?ordering=student_number"))
    assert ascending[0] == "Noor Adel"  # an empty number sorts first
    assert ascending[-1] == "Normal Person"
    assert _names(client.get(f"{URL}?ordering=-student_number"))[0] == "Normal Person"


def test_list_and_summary_query_count_is_constant(client, world):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    def count(url):
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(url)
        assert response.status_code == 200
        return len(ctx), len(response.data["data"]["results"]) if "results" in response.data["data"] else 0

    small, rows_small = count(f"{URL}?page_size=100")
    summary_small, _ = count(SUMMARY)
    for n in range(20, 50):  # many more students (each with groups and enrollments) must not add queries
        user = _student(world, n, number=f"22{n:05d}")
        GroupMembership.objects.create(group=world.group, user=user, status=GroupMembershipStatus.APPROVED)
        EnrollmentRequest.objects.create(
            user=user, student_profile=user.student_profile, faculty=world.fac, screenshot="e/x.png"
        )
    big, rows_big = count(f"{URL}?page_size=100")
    summary_big, _ = count(SUMMARY)
    assert rows_big == rows_small + 30
    assert big == small <= 6, (small, big)
    assert summary_big == summary_small


def test_permissions(world):
    anonymous = APIClient()
    assert anonymous.get(URL).status_code == 401
    assert anonymous.get(SUMMARY).status_code == 401
    for user in (world.ali, world.normal):
        c = APIClient()
        c.force_authenticate(user)
        assert c.get(URL).status_code == 403
        assert c.get(SUMMARY).status_code == 403
    for role in (UserRole.PRINT_STAFF, UserRole.SUPPORT_STAFF, UserRole.CONTENT_MANAGER):
        c = APIClient()
        c.force_authenticate(_mk_user(950 + len(role), role, email=f"{role}@students.test"))
        assert c.get(URL).status_code == 403, role
        assert c.get(SUMMARY).status_code == 403, role


def test_verification_review_capability_alone_is_enough(world):
    from apps.accounts.choices import PermissionEffect
    from apps.accounts.models import UserPermissionOverride
    from apps.accounts.permissions import Capability

    reviewer = _mk_user(960, UserRole.SUPPORT_STAFF, email="reviewer@students.test")
    UserPermissionOverride.objects.create(
        user=reviewer, permission_code=Capability.VERIFICATION_REVIEW, effect=PermissionEffect.ALLOW
    )
    c = APIClient()
    c.force_authenticate(reviewer)
    assert c.get(URL).status_code == 200
    assert c.get(SUMMARY).status_code == 200


def test_student_detail_route_is_not_exposed_here(client, world):
    assert client.get(f"{URL}{world.ali.pk}/").status_code == 404
