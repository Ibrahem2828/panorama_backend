"""Student number uniqueness (serializer + database + migration guard) and verification resubmission rules."""

from __future__ import annotations

import importlib
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.audit.models import AuditAction, AuditLog
from apps.product.models import AccountDeletionRequest
from apps.product.services import AccountDeletionService
from apps.universities.models import AcademicYear, Faculty, Major, Semester, University
from apps.verification.models import VerificationRequest, VerificationStatus

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)
NUMBER = "2150094"
OTHER_NUMBER = "2150095"


def make_student(n: int, number: str = "", status_: str = StudentVerificationStatus.INCOMPLETE) -> User:
    user = User.objects.create_user(
        full_name=f"Student {n}",
        email=f"student-identity-{n}@example.com",
        phone_number=f"+96395000{n:04d}",
        password="StrongPass123!",
        role=UserRole.STUDENT,
        is_email_verified=True,
    )
    StudentProfile.objects.create(user=user, student_number=number, verification_status=status_)
    return user


@pytest.fixture
def academic(db):
    university = University.objects.create(name="Damascus University", code="DU")
    faculty = Faculty.objects.create(university=university, name="Engineering", code="2")
    major = Major.objects.create(faculty=faculty, name="Software Engineering", code="SWE")
    year = AcademicYear.objects.create(name="First Year", order=1)
    semester = Semester.objects.create(name="First Semester", order=1)
    return {"university": university, "faculty": faculty, "major": major, "year": year, "semester": semester}


def submit_payload(academic, number: str = NUMBER) -> dict:
    return {
        "university": academic["university"].id,
        "faculty": academic["faculty"].id,
        "major": academic["major"].id,
        "academic_year": academic["year"].id,
        "semester": academic["semester"].id,
        "student_number": number,
        "card_image": SimpleUploadedFile("card.png", PNG_BYTES, content_type="image/png"),
    }


def client_for(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


# ---------------------------------------------------------------- student number uniqueness


@pytest.mark.django_db
def test_profile_update_rejects_a_number_used_by_another_live_student():
    make_student(1, NUMBER)
    second = make_student(2)
    response = client_for(second).patch("/api/v1/students/me/profile/", {"student_number": NUMBER}, format="json")
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "student_number" in response.data["errors"]
    # Nothing about the owner leaks.
    body = str(response.data)
    assert "student-identity-1" not in body and "Student 1" not in body
    second.student_profile.refresh_from_db()
    assert second.student_profile.student_number == ""


@pytest.mark.django_db
def test_profile_update_allows_own_number_and_audits_changed_fields_only():
    student = make_student(3)
    client = client_for(student)
    response = client.patch("/api/v1/students/me/profile/", {"student_number": NUMBER}, format="json")
    assert response.status_code == status.HTTP_200_OK
    again = client.patch("/api/v1/students/me/profile/", {"student_number": NUMBER}, format="json")
    assert again.status_code == status.HTTP_200_OK  # re-saving your own number is not a duplicate
    rows = AuditLog.objects.filter(action=AuditAction.STUDENT_PROFILE_UPDATED)
    assert rows.count() == 2
    assert rows.first().new_value == {"changed_fields": ["student_number"]}
    assert NUMBER not in str(rows.first().new_value)


@pytest.mark.django_db
def test_soft_deleted_profile_does_not_block_reuse():
    gone = make_student(4, NUMBER)
    StudentProfile.objects.filter(pk=gone.student_profile.pk).update(is_deleted=True)
    successor = make_student(5)
    response = client_for(successor).patch("/api/v1/students/me/profile/", {"student_number": NUMBER}, format="json")
    assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_anonymized_account_releases_its_number():
    leaving = make_student(6, NUMBER)
    deletion = AccountDeletionService.request(leaving)
    AccountDeletionRequest.objects.filter(pk=deletion.pk).update(scheduled_for=timezone.now() - timedelta(minutes=1))
    assert AccountDeletionService.execute_due() == 1
    leaving.student_profile.refresh_from_db()
    assert leaving.student_profile.student_number == ""

    successor = make_student(7)
    response = client_for(successor).patch("/api/v1/students/me/profile/", {"student_number": NUMBER}, format="json")
    assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_database_constraint_blocks_duplicates_but_not_blanks_or_deleted_rows():
    make_student(8, NUMBER)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_student(9, NUMBER)
    make_student(10, "")
    make_student(11, "")
    deleted = make_student(12, OTHER_NUMBER)
    StudentProfile.objects.filter(pk=deleted.student_profile.pk).update(is_deleted=True)
    make_student(13, OTHER_NUMBER)  # allowed: the first holder is soft-deleted


@pytest.mark.django_db
def test_student_registration_rejects_a_taken_number():
    make_student(14, NUMBER)
    response = APIClient().post(
        reverse("register-student"),
        {
            "full_name": "New Student",
            "email": "new-student-identity@example.com",
            "phone_number": "+963950009999",
            "password": "StrongPass123!",
            "password_confirm": "StrongPass123!",
            "student_number": NUMBER,
        },
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "student_number" in response.data["errors"]
    assert not User.objects.filter(email="new-student-identity@example.com").exists()


@pytest.mark.django_db
def test_verification_submit_rejects_a_taken_number(academic):
    make_student(15, NUMBER)
    student = make_student(16)
    response = client_for(student).post("/api/v1/verification/submit/", submit_payload(academic), format="multipart")
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "student_number" in response.data["errors"]
    assert not VerificationRequest.objects.filter(user=student).exists()


def _migration():
    return importlib.import_module("apps.accounts.migrations.0006_unique_student_number")


def _fake_apps(duplicate_rows: list[dict]):
    duplicates = MagicMock()
    duplicates.count.return_value = len(duplicate_rows)
    duplicates.__iter__.side_effect = lambda: iter(duplicate_rows)
    model = MagicMock()
    chain = model.objects.filter.return_value.exclude.return_value.values.return_value
    chain.annotate.return_value.filter.return_value = duplicates
    apps = MagicMock()
    apps.get_model.return_value = model
    return apps


def test_migration_refuses_with_an_actionable_message_when_duplicates_exist():
    with pytest.raises(RuntimeError) as excinfo:
        _migration().refuse_if_duplicates_exist(_fake_apps([{"total": 2}, {"total": 3}]), None)
    message = str(excinfo.value)
    assert "2 student number(s)" in message and "5 live student profiles" in message
    assert "migrate again" in message


def test_migration_passes_when_there_are_no_duplicates_and_is_reversible():
    migration = _migration()
    migration.refuse_if_duplicates_exist(_fake_apps([]), None)
    run_python = migration.Migration.operations[0]
    assert run_python.reverse_code is not None  # RunPython.noop: the constraint removal is the whole reverse


# ---------------------------------------------------------------- verification resubmission


@pytest.mark.django_db
def test_incomplete_student_can_submit(academic):
    student = make_student(20)
    response = client_for(student).post("/api/v1/verification/submit/", submit_payload(academic), format="multipart")
    assert response.status_code == status.HTTP_201_CREATED
    student.student_profile.refresh_from_db()
    assert student.student_profile.verification_status == StudentVerificationStatus.PENDING


@pytest.mark.django_db
def test_pending_student_cannot_create_a_second_request(academic):
    student = make_student(21)
    client = client_for(student)
    assert client.post("/api/v1/verification/submit/", submit_payload(academic), format="multipart").status_code == 201
    second = client.post("/api/v1/verification/submit/", submit_payload(academic), format="multipart")
    assert second.status_code == status.HTTP_409_CONFLICT
    assert second.data["code"] == "VERIFICATION_ALREADY_PENDING"
    assert VerificationRequest.objects.filter(user=student).count() == 1


@pytest.mark.django_db
def test_approved_student_cannot_resubmit_and_keeps_access(academic):
    student = make_student(22, NUMBER, StudentVerificationStatus.APPROVED)
    response = client_for(student).post("/api/v1/verification/submit/", submit_payload(academic), format="multipart")
    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.data["code"] == "VERIFICATION_ALREADY_APPROVED"
    student.student_profile.refresh_from_db()
    assert student.student_profile.verification_status == StudentVerificationStatus.APPROVED
    assert not VerificationRequest.objects.filter(user=student).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("state", [StudentVerificationStatus.REJECTED, StudentVerificationStatus.NEEDS_UPDATE])
def test_rejected_or_needs_update_student_can_resubmit(academic, state):
    student = make_student(23, "", state)
    response = client_for(student).post("/api/v1/verification/submit/", submit_payload(academic), format="multipart")
    assert response.status_code == status.HTTP_201_CREATED
    student.student_profile.refresh_from_db()
    assert student.student_profile.verification_status == StudentVerificationStatus.PENDING


@pytest.mark.django_db
def test_suspended_student_cannot_resubmit(academic):
    student = make_student(24, "", StudentVerificationStatus.SUSPENDED)
    response = client_for(student).post("/api/v1/verification/submit/", submit_payload(academic), format="multipart")
    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.data["code"] == "VERIFICATION_NOT_RESUBMITTABLE"


@pytest.mark.django_db
def test_staff_reopening_an_approved_student_lets_them_resubmit(academic):
    staff = User.objects.create_user(
        full_name="Reviewer",
        email="reviewer-identity@example.com",
        phone_number="+963950008888",
        password="StrongPass123!",
        role=UserRole.ADMIN,
    )
    student = make_student(25)
    client = client_for(student)
    assert client.post("/api/v1/verification/submit/", submit_payload(academic), format="multipart").status_code == 201
    request = VerificationRequest.objects.get(user=student)

    reviewer = client_for(staff)
    assert reviewer.post(f"/api/v1/dashboard/verifications/{request.id}/approve/", {}, format="json").status_code == 200
    assert client.post("/api/v1/verification/submit/", submit_payload(academic), format="multipart").status_code == 409

    reopened = reviewer.post(
        f"/api/v1/dashboard/verifications/{request.id}/needs-update/",
        {"rejection_reason": "Card photo is unreadable"},
        format="json",
    )
    assert reopened.status_code == status.HTTP_200_OK
    request.refresh_from_db()
    assert request.status == VerificationStatus.NEEDS_UPDATE
    student.student_profile.refresh_from_db()
    assert student.student_profile.verification_status == StudentVerificationStatus.NEEDS_UPDATE

    again = client.post("/api/v1/verification/submit/", submit_payload(academic), format="multipart")
    assert again.status_code == status.HTTP_201_CREATED


@pytest.mark.django_db
def test_reject_still_only_applies_to_pending_requests(academic):
    staff = User.objects.create_user(
        full_name="Reviewer 2",
        email="reviewer2-identity@example.com",
        phone_number="+963950007777",
        password="StrongPass123!",
        role=UserRole.ADMIN,
    )
    student = make_student(26)
    client_for(student).post("/api/v1/verification/submit/", submit_payload(academic), format="multipart")
    request = VerificationRequest.objects.get(user=student)
    reviewer = client_for(staff)
    reviewer.post(f"/api/v1/dashboard/verifications/{request.id}/approve/", {}, format="json")
    rejected = reviewer.post(
        f"/api/v1/dashboard/verifications/{request.id}/reject/", {"rejection_reason": "late"}, format="json"
    )
    assert rejected.status_code == status.HTTP_400_BAD_REQUEST
