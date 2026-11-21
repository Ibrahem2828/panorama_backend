"""Group images are served through ``/api/v1/groups/{id}/image/`` under the group detail visibility rules."""

from __future__ import annotations

from io import BytesIO

import pytest
from django.core.files.base import ContentFile
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.universities.models import AcademicYear, Faculty, Major, University

from .models import Group, GroupMembership, GroupMembershipStatus


def _user(role, n):
    return User.objects.create_user(
        full_name=f"Grp {role}",
        email=f"grpimg-{n}@example.test",
        phone_number=f"+96394445{n:04d}",
        password="StrongPass123!",
        role=role,
    )


def _client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def _png_bytes():
    buffer = BytesIO()
    Image.new("RGB", (10, 10), "green").save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)


@pytest.fixture
def world(db):
    uni = University.objects.create(name="U", code="U1")
    other_uni = University.objects.create(name="U2", code="U2")
    fac = Faculty.objects.create(university=uni, name="F1", code="5")
    major = Major.objects.create(faculty=fac, name="M", code="M1")
    year = AcademicYear.objects.create(name="Y1", order=1)
    admin = _user(UserRole.ADMIN, 1)
    student = _user(UserRole.STUDENT, 2)
    StudentProfile.objects.create(
        user=student,
        university=uni,
        faculty=fac,
        major=major,
        academic_year=year,
        verification_status=StudentVerificationStatus.APPROVED,
        verified_at=timezone.now(),
    )
    unverified = _user(UserRole.STUDENT, 3)
    StudentProfile.objects.create(user=unverified, university=uni, faculty=fac)
    return {
        "admin": admin,
        "uni": uni,
        "other_uni": other_uni,
        "year": year,
        "student_user": student,
        "student": _client(student),
        "staff": _client(admin),
        "unverified": _client(unverified),
        "normal": _client(_user(UserRole.NORMAL_USER, 4)),
    }


def _group(world, *, image=True, **extra):
    extra.setdefault("university", world["uni"])
    row = Group.objects.create(name="g", created_by=world["admin"], **extra)
    if image:
        row.image.save("g.png", ContentFile(_png_bytes()))
    return row


def _url(row):
    return f"/api/v1/groups/{row.pk}/image/"


def test_requires_authentication(world):
    assert APIClient().get(_url(_group(world))).status_code in (401, 403)


def test_served_with_headers_etag_and_304(world):
    row = _group(world)
    response = world["student"].get(_url(row))
    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Cache-Control"].startswith("private")
    assert b"".join(response.streaming_content) == _png_bytes()
    assert world["student"].get(_url(row), HTTP_IF_NONE_MATCH=response["ETag"]).status_code == 304


def test_groups_the_student_may_not_see_are_404_idor(world):
    other_university = _group(world, university=world["other_uni"])
    inactive = _group(world, is_active=False)
    deleted = _group(world, is_deleted=True)
    level = _group(world, academic_year=world["year"])  # level group: members only
    for row in (other_university, inactive, deleted, level):
        assert world["student"].get(_url(row)).status_code == 404
    GroupMembership.objects.create(group=level, user=world["student_user"], status=GroupMembershipStatus.APPROVED)
    assert world["student"].get(_url(level)).status_code == 200


def test_non_students_and_unverified_students_are_404_but_staff_see_everything(world):
    row = _group(world, is_active=False)
    assert world["normal"].get(_url(row)).status_code == 404
    assert world["unverified"].get(_url(_group(world))).status_code == 404
    assert world["staff"].get(_url(row)).status_code == 200
    assert world["staff"].get(_url(_group(world, is_deleted=True))).status_code == 404


def test_no_image_or_non_image_bytes_are_404(world):
    assert world["student"].get(_url(_group(world, image=False))).status_code == 404
    fake = _group(world, image=False)
    fake.image.save("g.png", ContentFile(b"<html></html>"))
    assert world["student"].get(_url(fake)).status_code == 404


def test_serializers_return_the_authenticated_route_not_media(world):
    row = _group(world)
    plain = _group(world, image=False)
    detail = world["student"].get(f"/api/v1/groups/{row.pk}/").json()["data"]
    assert f"/api/v1/groups/{row.pk}/image/?v=" in detail["image"]
    assert "/media/" not in detail["image"]
    listing = world["student"].get("/api/v1/groups/").json()["data"]["results"]
    by_id = {item["id"]: item for item in listing}
    assert by_id[plain.pk]["image"] is None
    dash = world["staff"].get(f"/api/v1/dashboard/groups/{row.pk}/").json()["data"]
    assert f"/api/v1/groups/{row.pk}/image/?v=" in dash["image"]
