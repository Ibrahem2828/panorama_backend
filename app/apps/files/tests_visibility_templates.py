"""File visibility templates: saved audiences for uploads."""

import pytest
from rest_framework.test import APIClient

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.universities.models import AcademicYear, Faculty, Major, University

URL = "/api/v1/dashboard/file-visibility-templates/"


@pytest.fixture
def staff(db):
    return User.objects.create_user(
        full_name="Files admin",
        email="files-admin@example.test",
        phone_number="+963977700001",
        password="StrongPass123!",
        role=UserRole.ADMIN,
    )


def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def test_create_list_and_scope_validation(staff):
    university = University.objects.create(name="U", code="U1")
    faculty = Faculty.objects.create(university=university, name="Informatics", code="INF")
    major = Major.objects.create(faculty=faculty, name="Software")
    year = AcademicYear.objects.create(name="Year 2", order=2)
    api = client(staff)
    created = api.post(
        URL,
        {
            "name": "Verified Informatics Y2",
            "visibility": "major_only",
            "university": university.pk,
            "faculty": faculty.pk,
            "major": major.pk,
            "academic_year": year.pk,
        },
        format="json",
    )
    assert created.status_code == 201, created.json()
    listed = api.get(URL).json()["data"]
    rows = listed["results"] if isinstance(listed, dict) else listed
    assert [row["name"] for row in rows] == ["Verified Informatics Y2"]
    # major-only needs a major and a year
    assert api.post(URL, {"name": "Broken", "visibility": "major_only"}, format="json").status_code == 400
    # group-only needs a group
    assert api.post(URL, {"name": "Broken 2", "visibility": "group_only"}, format="json").status_code == 400


def test_names_are_unique_and_students_are_refused(staff):
    api = client(staff)
    assert api.post(URL, {"name": "Same", "visibility": "public"}, format="json").status_code == 201
    assert api.post(URL, {"name": "Same", "visibility": "public"}, format="json").status_code == 400
    student = User.objects.create_user(
        full_name="S",
        email="s-files@example.test",
        phone_number="+963977700002",
        password="StrongPass123!",
        role=UserRole.STUDENT,
    )
    assert client(student).get(URL).status_code == 403
