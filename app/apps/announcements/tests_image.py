"""Announcement images are served through ``/api/v1/announcements/{id}/image/`` under the feed visibility rules."""

from __future__ import annotations

from datetime import timedelta
from io import BytesIO

import pytest
from django.core.files.base import ContentFile
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.universities.models import Faculty, University

from .models import Announcement, AnnouncementTargetUserType


def _user(role, n):
    return User.objects.create_user(
        full_name=f"Ann {role}",
        email=f"annimg-{n}@example.test",
        phone_number=f"+96394446{n:04d}",
        password="StrongPass123!",
        role=role,
    )


def _client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def _png_bytes():
    buffer = BytesIO()
    Image.new("RGB", (10, 10), "blue").save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)


@pytest.fixture
def world(db):
    uni = University.objects.create(name="U", code="U1")
    fac = Faculty.objects.create(university=uni, name="F1", code="5")
    other_fac = Faculty.objects.create(university=uni, name="F2", code="6")
    admin = _user(UserRole.ADMIN, 1)
    student = _user(UserRole.STUDENT, 2)
    StudentProfile.objects.create(
        user=student,
        university=uni,
        faculty=fac,
        verification_status=StudentVerificationStatus.APPROVED,
        verified_at=timezone.now(),
    )
    return {"admin": admin, "student": _client(student), "staff": _client(admin), "fac": fac, "other_fac": other_fac}


def _announcement(world, *, image=True, **extra):
    row = Announcement.objects.create(title="t", description="d", created_by=world["admin"], **extra)
    if image:
        row.image.save("a.png", ContentFile(_png_bytes()))
    return row


def _url(row):
    return f"/api/v1/announcements/{row.pk}/image/"


def test_requires_authentication(world):
    assert APIClient().get(_url(_announcement(world))).status_code in (401, 403)


def test_served_with_headers_etag_and_304(world):
    row = _announcement(world)
    response = world["student"].get(_url(row))
    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Cache-Control"].startswith("private")
    assert b"".join(response.streaming_content) == _png_bytes()
    assert world["student"].get(_url(row), HTTP_IF_NONE_MATCH=response["ETag"]).status_code == 304


def test_hidden_announcements_are_404_for_students_idor(world):
    admins_only = _announcement(world, target_user_type=AnnouncementTargetUserType.ADMINS)
    other_faculty = _announcement(world, target_faculty=world["other_fac"])
    inactive = _announcement(world, is_active=False)
    expired = _announcement(world, ends_at=timezone.now() - timedelta(days=1))
    deleted = _announcement(world, is_deleted=True)
    for row in (admins_only, other_faculty, inactive, expired, deleted):
        assert world["student"].get(_url(row)).status_code == 404
    own_faculty = _announcement(world, target_faculty=world["fac"])
    assert world["student"].get(_url(own_faculty)).status_code == 200


def test_staff_see_hidden_but_not_deleted(world):
    inactive = _announcement(world, is_active=False)
    deleted = _announcement(world, is_deleted=True)
    assert world["staff"].get(_url(inactive)).status_code == 200
    assert world["staff"].get(_url(deleted)).status_code == 404


def test_no_image_or_non_image_bytes_are_404(world):
    assert world["student"].get(_url(_announcement(world, image=False))).status_code == 404
    fake = Announcement.objects.create(title="x", description="d", created_by=world["admin"])
    fake.image.save("a.png", ContentFile(b"<html></html>"))
    assert world["student"].get(_url(fake)).status_code == 404


def test_serializers_return_the_authenticated_route_not_media(world):
    row = _announcement(world)
    plain = _announcement(world, image=False)
    listing = world["student"].get("/api/v1/announcements/").json()["data"]["results"]
    by_id = {item["id"]: item for item in listing}
    assert f"/api/v1/announcements/{row.pk}/image/?v=" in by_id[row.pk]["image"]
    assert "/media/" not in by_id[row.pk]["image"]
    assert by_id[plain.pk]["image"] is None
    dash = world["staff"].get(f"/api/v1/dashboard/announcements/{row.pk}/").json()["data"]
    assert f"/api/v1/announcements/{row.pk}/image/?v=" in dash["image"]
