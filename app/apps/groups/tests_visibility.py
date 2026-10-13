"""Group collection visibility: a student only sees groups of their own university they are eligible for."""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.groups.models import Group, GroupMembership, GroupMembershipStatus
from apps.groups.services import eligible_groups_filter, is_student_eligible_for_group
from apps.universities.models import AcademicYear, Faculty, Major, Semester, University


def _student(email: str, phone: str, *, university, faculty=None, major=None, year=None, semester=None) -> User:
    user = User.objects.create_user(
        full_name=email, email=email, phone_number=phone, password="StrongPass123!", role=UserRole.STUDENT
    )
    StudentProfile.objects.create(
        user=user,
        university=university,
        faculty=faculty,
        major=major,
        academic_year=year,
        semester=semester,
        verification_status=StudentVerificationStatus.APPROVED,
        verified_at=timezone.now(),
    )
    return user


@pytest.fixture
def world(db):
    uni_a = University.objects.create(name="Uni A", code="UA")
    uni_b = University.objects.create(name="Uni B", code="UB")
    fac_a = Faculty.objects.create(university=uni_a, name="Eng A", code="2")
    fac_a2 = Faculty.objects.create(university=uni_a, name="Sci A", code="3")
    fac_b = Faculty.objects.create(university=uni_b, name="Eng B", code="2")
    major_a = Major.objects.create(faculty=fac_a, name="SWE", code="SWE")
    year = AcademicYear.objects.create(name="Y1", order=1)
    year2 = AcademicYear.objects.create(name="Y2", order=2)
    semester = Semester.objects.create(name="S1", order=1)
    admin = User.objects.create_user(
        full_name="Admin", email="adm@example.test", phone_number="+963900000001", password="x" * 12, role="admin"
    )
    me = _student(
        "me@example.test",
        "+963900000010",
        university=uni_a,
        faculty=fac_a,
        major=major_a,
        year=year,
        semester=semester,
    )
    mk = lambda name, **kw: Group.objects.create(name=name, created_by=admin, **kw)  # noqa: E731
    groups = {
        "uni_wide": mk("Uni wide A", university=uni_a),
        "faculty": mk("Faculty A", university=uni_a, faculty=fac_a),
        "major": mk("Major A", university=uni_a, faculty=fac_a, major=major_a),
        "other_faculty": mk("Other faculty A", university=uni_a, faculty=fac_a2),
        "other_year": mk("Other year A", university=uni_a, academic_year=year2),
        "other_uni": mk("Secret B", university=uni_b),
        "other_uni_faculty": mk("Secret B Eng", university=uni_b, faculty=fac_b),
        "inactive": mk("Inactive A", university=uni_a, is_active=False),
        "deleted": mk("Deleted A", university=uni_a, is_deleted=True),
    }
    client = APIClient()
    client.force_authenticate(me)
    return {"me": me, "client": client, "groups": groups, "uni_a": uni_a, "uni_b": uni_b, "admin": admin}


def _ids(response) -> set[int]:
    return {item["id"] for item in response.data["data"]["results"]}


def test_list_contains_only_own_university_eligible_groups(world):
    g = world["groups"]
    response = world["client"].get("/api/v1/groups/")
    assert response.status_code == 200
    assert _ids(response) == {g["uni_wide"].id, g["faculty"].id, g["major"].id}


def test_list_matches_the_python_eligibility_rule(world):
    g = world["groups"]
    for group in g.values():
        group.refresh_from_db()
    visible = set(Group.objects.filter(eligible_groups_filter(world["me"])).values_list("id", flat=True))
    expected = {grp.id for grp in Group.objects.all() if is_student_eligible_for_group(world["me"], grp)}
    assert visible == expected


def test_list_never_leaks_other_universities_via_search_filter_or_ordering_params(world):
    """Query params must never widen the collection beyond what the student may see (whether or not they filter)."""
    g = world["groups"]
    visible = {g["uni_wide"].id, g["faculty"].id, g["major"].id}
    client = world["client"]
    probes = [
        {"search": "Secret"},
        {"search": "B"},
        {"university": world["uni_b"].id},
        {"faculty": g["other_uni_faculty"].faculty_id},
        {"university": world["uni_b"].id, "ordering": "-name"},
        {"is_active": "false"},
    ]
    for params in probes:
        response = client.get("/api/v1/groups/", params)
        assert response.status_code == 200, params
        assert _ids(response) <= visible, params
        assert g["other_uni"].id not in _ids(response), params
        assert g["other_uni_faculty"].id not in _ids(response), params


def test_retrieve_other_university_group_is_forbidden_and_own_is_allowed(world):
    g = world["groups"]
    client = world["client"]
    assert client.get(f"/api/v1/groups/{g['other_uni'].id}/").status_code == 403
    assert client.get(f"/api/v1/groups/{g['other_faculty'].id}/").status_code == 403
    assert client.get(f"/api/v1/groups/{g['inactive'].id}/").status_code == 404
    assert client.get(f"/api/v1/groups/{g['major'].id}/").status_code == 200


def test_groups_the_student_already_belongs_to_stay_listed(world):
    g = world["groups"]
    GroupMembership.objects.create(group=g["other_year"], user=world["me"], status=GroupMembershipStatus.APPROVED)
    GroupMembership.objects.create(group=g["other_faculty"], user=world["me"], status=GroupMembershipStatus.PENDING)
    ids = _ids(world["client"].get("/api/v1/groups/"))
    assert g["other_year"].id in ids
    assert g["other_faculty"].id not in ids  # a pending request does not grant visibility


def test_list_reports_correct_member_counts_and_status_without_extra_row_duplication(world):
    g = world["groups"]
    other = _student("o@example.test", "+963900000011", university=world["uni_a"])
    GroupMembership.objects.create(group=g["uni_wide"], user=other, status=GroupMembershipStatus.APPROVED)
    GroupMembership.objects.create(group=g["uni_wide"], user=world["me"], status=GroupMembershipStatus.APPROVED)
    response = world["client"].get("/api/v1/groups/")
    rows = [r for r in response.data["data"]["results"] if r["id"] == g["uni_wide"].id]
    assert len(rows) == 1
    assert rows[0]["members_count"] == 2
    assert rows[0]["membership_status"] == GroupMembershipStatus.APPROVED


def test_list_query_count_does_not_grow_with_the_number_of_groups(world):
    client = world["client"]
    client.get("/api/v1/groups/")  # warm up
    with CaptureQueriesContext(connection) as before:
        client.get("/api/v1/groups/")
    for i in range(15):
        Group.objects.create(name=f"Extra {i}", university=world["uni_a"], created_by=world["admin"])
    with CaptureQueriesContext(connection) as after:
        response = client.get("/api/v1/groups/")
    assert len(_ids(response)) >= 15
    assert len(after) <= len(before) + 1


def test_unverified_or_non_student_users_are_still_rejected(world):
    normal = User.objects.create_user(
        full_name="N", email="n@example.test", phone_number="+963900000012", password="x" * 12, role="normal_user"
    )
    client = APIClient()
    client.force_authenticate(normal)
    assert client.get("/api/v1/groups/").status_code == 403
    assert APIClient().get("/api/v1/groups/").status_code == 401


def test_a_student_of_another_university_sees_only_their_own_groups(world):
    g = world["groups"]
    other = _student("b@example.test", "+963900000013", university=world["uni_b"])
    client = APIClient()
    client.force_authenticate(other)
    assert _ids(client.get("/api/v1/groups/")) == {g["other_uni"].id}


def test_dashboard_group_endpoints_are_not_affected_by_student_scoping(world):
    g = world["groups"]
    client = APIClient()
    client.force_authenticate(world["admin"])
    response = client.get("/api/v1/dashboard/groups/")
    assert response.status_code == 200
    ids = {item["id"] for item in response.data["data"]["results"]}
    assert {g["uni_wide"].id, g["other_uni"].id, g["inactive"].id} <= ids
    assert response.data["data"]["count"] >= 8
