"""Student subject catalogue: ``GET subjects/`` and ``GET subjects/<id>/`` (informational, faculty-scoped)."""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.enrollment.models import EnrollmentItem, EnrollmentItemStatus, EnrollmentRequest
from apps.groups.models import Group
from apps.universities.models import AcademicYear, CurrentTerm, Faculty, Major, Semester, Subject, University

pytestmark = pytest.mark.django_db

URL = "/api/v1/subjects/"


def _user(email, role, n, name):
    return User.objects.create_user(
        full_name=name, email=email, phone_number=f"+9639920{n:05d}", password="StrongPass123!", role=role
    )


def _client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def w():
    uni = University.objects.create(name="Uni", code="U")
    faculty = Faculty.objects.create(university=uni, name="Dentistry", code="DEN")
    other = Faculty.objects.create(university=uni, name="Law", code="LAW")
    major = Major.objects.create(faculty=faculty, name="Ortho", code="ORT")
    y1 = AcademicYear.objects.create(name="Year 1", order=1)
    y2 = AcademicYear.objects.create(name="Year 2", order=2)
    s1 = Semester.objects.create(name="Semester 1", order=1)
    s2 = Semester.objects.create(name="Semester 2", order=2)
    student = _user("s@t.test", UserRole.STUDENT, 1, "Sara")
    profile = StudentProfile.objects.create(
        user=student,
        university=uni,
        faculty=faculty,
        academic_year=y1,
        semester=s1,
        student_number="2150001",
        verification_status=StudentVerificationStatus.APPROVED,
    )
    admin = _user("a@t.test", UserRole.ADMIN, 2, "Admin")

    def subject(name, code, year, sem, fac=faculty, **kw):
        return Subject.objects.create(faculty=fac, academic_year=year, semester=sem, name=name, code=code, **kw)

    subjects = {
        "anat": subject("Anatomy", "A1", y1, s1, description="Full anatomy course."),
        "mat": subject("Materials", "M2", y1, s2),
        "perio": subject("Periodontics", "P3", y2, s1, major=major),
        "hidden": subject("Hidden", "H4", y1, s1, is_active=False),
        "deleted": subject("Deleted", "D5", y1, s1, is_deleted=True),
        "law": subject("Civil Law", "L6", y1, s1, fac=other),
    }
    CurrentTerm.objects.update_or_create(pk=CurrentTerm.SINGLETON_PK, defaults={"semester": s2})
    return {
        "uni": uni,
        "faculty": faculty,
        "student": student,
        "profile": profile,
        "admin": admin,
        "s1": s1,
        "s2": s2,
        "y1": y1,
        "y2": y2,
        "subjects": subjects,
    }


def _rows(response):
    assert response.status_code == 200, response.data
    return response.data["data"]["results"]


def test_lists_only_active_subjects_of_the_students_faculty_with_row_shape(w):
    rows = _rows(_client(w["student"]).get(URL))
    assert {r["name"] for r in rows} == {"Anatomy", "Materials", "Periodontics"}
    anat = next(r for r in rows if r["name"] == "Anatomy")
    assert anat == {
        "id": w["subjects"]["anat"].pk,
        "name": "Anatomy",
        "code": "A1",
        "academic_year": {"id": w["y1"].pk, "name": "Year 1", "order": 1},
        "semester": {"id": w["s1"].pk, "name": "Semester 1", "order": 1},
        "major": None,
        "faculty": {"id": w["faculty"].pk, "name": "Dentistry"},
        "in_current_term": False,
        "is_enrolled": False,
        "has_description": True,
    }
    perio = next(r for r in rows if r["name"] == "Periodontics")
    assert perio["major"]["name"] == "Ortho" and perio["has_description"] is False
    assert "description" not in anat and not any("group" in key or "join" in key for key in anat)


def test_default_order_is_current_term_first_then_year_semester_name(w):
    names = [r["name"] for r in _rows(_client(w["student"]).get(URL))]
    assert names == ["Materials", "Anatomy", "Periodontics"]  # current term = Semester 2
    descending = [r["name"] for r in _rows(_client(w["student"]).get(URL, {"ordering": "-name"}))]
    assert descending == ["Periodontics", "Materials", "Anatomy"]


def test_filters(w):
    client = _client(w["student"])
    assert {r["name"] for r in _rows(client.get(URL, {"academic_year": w["y2"].pk}))} == {"Periodontics"}
    assert {r["name"] for r in _rows(client.get(URL, {"semester": w["s2"].pk}))} == {"Materials"}
    assert {r["name"] for r in _rows(client.get(URL, {"search": "p3"}))} == {"Periodontics"}
    assert {r["name"] for r in _rows(client.get(URL, {"search": "anat"}))} == {"Anatomy"}
    assert {r["name"] for r in _rows(client.get(URL, {"in_current_term": "true"}))} == {"Materials"}
    assert {r["name"] for r in _rows(client.get(URL, {"in_current_term": "false"}))} == {"Anatomy", "Periodontics"}
    assert client.get(URL, {"enrolled": "maybe"}).status_code == 400
    assert client.get(URL, {"academic_year": "x"}).status_code == 400


def test_enrolled_flag_and_filter_use_approved_items_only(w):
    enrollment = EnrollmentRequest.objects.create(
        user=w["student"],
        student_profile=w["profile"],
        faculty=w["faculty"],
        academic_year=w["y1"],
        semester=w["s1"],
        screenshot="x.png",
    )
    for key, item_status in (
        ("anat", EnrollmentItemStatus.APPROVED),
        ("mat", EnrollmentItemStatus.PENDING),
        ("perio", EnrollmentItemStatus.REJECTED),
    ):
        EnrollmentItem.objects.create(request=enrollment, subject=w["subjects"][key], status=item_status)
    client = _client(w["student"])
    assert {r["name"] for r in _rows(client.get(URL, {"enrolled": "true"}))} == {"Anatomy"}
    assert {r["name"] for r in _rows(client.get(URL, {"enrolled": "false"}))} == {"Materials", "Periodontics"}
    flags = {r["name"]: r["is_enrolled"] for r in _rows(client.get(URL))}
    assert flags == {"Anatomy": True, "Materials": False, "Periodontics": False}
    # another student's approval does not count for me
    other = _user("o@t.test", UserRole.STUDENT, 3, "Omar")
    StudentProfile.objects.create(
        user=other, university=w["uni"], faculty=w["faculty"], verification_status=StudentVerificationStatus.APPROVED
    )
    assert _rows(_client(other).get(URL, {"enrolled": "true"})) == []


def test_detail_adds_description_and_hides_other_faculties_and_inactive(w):
    client = _client(w["student"])
    detail = client.get(f"{URL}{w['subjects']['anat'].pk}/")
    assert detail.status_code == 200
    assert detail.data["data"]["description"] == "Full anatomy course."
    assert detail.data["data"]["code"] == "A1"
    for key in ("law", "hidden", "deleted"):
        assert client.get(f"{URL}{w['subjects'][key].pk}/").status_code == 404


def test_no_group_data_leaks_even_when_groups_exist(w):
    Group.objects.create(
        name="Anatomy group",
        university=w["uni"],
        faculty=w["faculty"],
        subject=w["subjects"]["anat"],
        created_by=w["admin"],
    )
    detail = _client(w["student"]).get(f"{URL}{w['subjects']['anat'].pk}/").data["data"]
    assert not any("group" in key or "join" in key for key in detail)


def test_only_verified_students_may_read(w):
    assert _client(w["admin"]).get(URL).status_code == 403
    assert APIClient().get(URL).status_code == 401
    w["profile"].verification_status = StudentVerificationStatus.PENDING
    w["profile"].save()
    assert _client(w["student"]).get(URL).status_code == 403


def test_query_count_is_constant(w):
    client = _client(w["student"])
    with CaptureQueriesContext(connection) as small:
        client.get(URL)
    for i in range(8):
        Subject.objects.create(
            faculty=w["faculty"], academic_year=w["y2"], semester=w["s2"], name=f"Extra {i}", code=f"E{i}"
        )
    with CaptureQueriesContext(connection) as large:
        assert len(_rows(client.get(URL))) == 11
    assert len(large) == len(small)
