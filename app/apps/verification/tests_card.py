"""The owner can view the verification card they uploaded; nobody else can, and the staff ticket flow is unchanged."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.files.base import ContentFile
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.universities.models import AcademicYear, Faculty, Major, Semester, University
from apps.verification.models import VerificationRequest

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
URL = "/api/v1/verification/me/card/"


def _user(email: str, n: int, role: str = UserRole.STUDENT) -> User:
    return User.objects.create_user(
        full_name=email.split("@")[0],
        email=email,
        phone_number=f"+96397700{n:04d}",
        password="StrongPass123!",
        role=role,
    )


@pytest.fixture
def world(db):
    university = University.objects.create(name="Card University", code="CU")
    faculty = Faculty.objects.create(university=university, name="Faculty", code="F")
    major = Major.objects.create(faculty=faculty, name="Major", code="M")
    year = AcademicYear.objects.create(name="Y1", order=81)
    semester = Semester.objects.create(name="S1", order=81)

    def make_request(user, content: bytes, name: str, status_value="pending"):
        profile, _ = StudentProfile.objects.get_or_create(
            user=user,
            defaults={
                "university": university,
                "faculty": faculty,
                "major": major,
                "academic_year": year,
                "semester": semester,
                "verification_status": StudentVerificationStatus.PENDING,
            },
        )
        return VerificationRequest.objects.create(
            user=user,
            student_profile=profile,
            university=university,
            faculty=faculty,
            major=major,
            academic_year=year,
            semester=semester,
            student_number=f"21{user.id:05d}",
            card_image=ContentFile(content, name=name),
            status=status_value,
        )

    return SimpleNamespace(
        make_request=make_request,
        owner=_user("card-owner@example.test", 1),
        other=_user("card-other@example.test", 2),
        normal=_user("card-normal@example.test", 3, UserRole.NORMAL_USER),
        reviewer=_user("card-reviewer@example.test", 4, UserRole.ADMIN),
    )


def _body(response: Any) -> bytes:
    return b"".join(response.streaming_content)


def _get(user, url=URL):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user)
    return client.get(url)


@pytest.mark.django_db
def test_owner_streams_their_own_card_with_private_headers(world):
    world.make_request(world.owner, PNG_BYTES, "card.png")
    response = _get(world.owner)
    assert response.status_code == status.HTTP_200_OK
    assert response["Content-Type"] == "image/png"
    assert response["Cache-Control"] == "private, no-store, max-age=0"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in response["Content-Security-Policy"]
    assert _body(response) == PNG_BYTES


@pytest.mark.django_db
def test_content_type_comes_from_the_bytes_not_the_filename(world):
    # A PNG stored under a misleading name is still served as image/png with a fixed, neutral download name.
    world.make_request(world.owner, PNG_BYTES, "evil.html")
    response = _get(world.owner)
    assert response.status_code == status.HTTP_200_OK
    assert response["Content-Type"] == "image/png"
    disposition = response["Content-Disposition"]
    assert disposition.startswith("inline") and "verification-card.png" in disposition
    assert "evil" not in disposition


@pytest.mark.django_db
def test_non_image_content_is_never_served(world):
    world.make_request(world.owner, b"<html><script>alert(1)</script></html>", "card.png")
    assert _get(world.owner).status_code == status.HTTP_404_NOT_FOUND
    VerificationRequest.objects.all().delete()
    world.make_request(world.owner, b"%PDF-1.4 not an image", "card.png")
    assert _get(world.owner).status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_other_users_get_their_own_card_or_404_never_the_owners(world):
    world.make_request(world.owner, PNG_BYTES, "owner.png")
    assert _get(world.other).status_code == status.HTTP_404_NOT_FOUND
    assert _get(world.normal).status_code == status.HTTP_404_NOT_FOUND
    assert _get(world.reviewer).status_code == status.HTTP_404_NOT_FOUND  # staff have no card of their own

    other_png = PNG_BYTES + b"\x00"  # distinguishable bytes, still a PNG by signature
    world.make_request(world.other, other_png, "other.png")
    mine = _get(world.other)
    assert _body(mine) == other_png
    assert _body(_get(world.owner)) == PNG_BYTES


@pytest.mark.django_db
def test_latest_request_wins_and_deleted_requests_are_ignored(world):
    older = world.make_request(world.owner, PNG_BYTES, "old.png", status_value="rejected")
    newer = world.make_request(world.owner, PNG_BYTES + b"\x01", "new.png")
    assert _body(_get(world.owner)) == PNG_BYTES + b"\x01"
    VerificationRequest.objects.filter(pk=newer.pk).update(is_deleted=True)
    assert _body(_get(world.owner)) == PNG_BYTES
    VerificationRequest.objects.filter(pk=older.pk).update(is_deleted=True)
    assert _get(world.owner).status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_requires_authentication_and_missing_request_is_404(world):
    assert _get(None).status_code == status.HTTP_401_UNAUTHORIZED
    assert _get(world.owner).status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_missing_stored_file_is_404_not_500(world):
    request = world.make_request(world.owner, PNG_BYTES, "gone.png")
    request.card_image.storage.delete(request.card_image.name)
    assert _get(world.owner).status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_staff_ticket_flow_is_unchanged_and_owner_cannot_use_it(world):
    request = world.make_request(world.owner, PNG_BYTES, "card.png")
    staff = APIClient()
    staff.force_authenticate(world.reviewer)
    ticket = staff.post(f"/api/v1/dashboard/verifications/{request.pk}/card-ticket/")
    assert ticket.status_code == status.HTTP_201_CREATED
    path = ticket.data["data"]["preview_url"].split("/api/v1/", 1)[1]
    streamed = staff.get(f"/api/v1/{path}")
    assert streamed.status_code == status.HTTP_200_OK
    assert _body(streamed) == PNG_BYTES

    owner = APIClient()
    owner.force_authenticate(world.owner)
    assert owner.post(f"/api/v1/dashboard/verifications/{request.pk}/card-ticket/").status_code == 403
    assert owner.get(f"/api/v1/{path}").status_code == status.HTTP_403_FORBIDDEN
