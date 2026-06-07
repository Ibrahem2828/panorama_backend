import importlib
import sys

import pytest
from decouple import UndefinedValueError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import OTPCode, StudentProfile, User
from apps.files.models import FileResource, FileVisibility
from apps.groups.models import Group, GroupMembership, GroupMembershipStatus
from apps.notifications.models import DeviceToken
from apps.printing.models import PrintOrder, PrintOrderStatus, PrintOrderStatusHistory
from apps.support.models import SupportTicket, SupportTicketStatus
from apps.universities.models import AcademicYear, Faculty, Major, Semester, University
from apps.verification.models import VerificationRequest, VerificationStatus


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(
        full_name="Reliability Admin",
        email="reliability-admin@example.com",
        phone_number="+963977100001",
        password="StrongPass123!",
        role=UserRole.ADMIN,
    )


@pytest.fixture
def print_staff(db):
    return User.objects.create_user(
        full_name="Reliability Print",
        email="reliability-print@example.com",
        phone_number="+963977100002",
        password="StrongPass123!",
        role=UserRole.PRINT_STAFF,
    )


@pytest.fixture
def normal_user(db):
    return User.objects.create_user(
        full_name="Reliability Normal",
        email="reliability-normal@example.com",
        phone_number="+963977100003",
        password="StrongPass123!",
        role=UserRole.NORMAL_USER,
    )


@pytest.fixture
def student_user(db):
    user = User.objects.create_user(
        full_name="Reliability Student",
        email="reliability-student@example.com",
        phone_number="+963977100004",
        password="StrongPass123!",
        role=UserRole.STUDENT,
        is_phone_verified=True,
    )
    StudentProfile.objects.create(user=user)
    return user


@pytest.fixture
def academic(db):
    university = University.objects.create(name="Reliability University", code="RU")
    faculty = Faculty.objects.create(university=university, name="Reliability Faculty", code="2")
    major = Major.objects.create(faculty=faculty, name="Reliability Major", code="RM")
    year = AcademicYear.objects.create(name="Reliability Year", order=10)
    semester = Semester.objects.create(name="Reliability Semester", order=10)
    return {"university": university, "faculty": faculty, "major": major, "year": year, "semester": semester}


def auth(client, user):
    client.force_authenticate(user=user)


def approve_student(user, academic):
    profile = user.student_profile
    profile.university = academic["university"]
    profile.faculty = academic["faculty"]
    profile.major = academic["major"]
    profile.academic_year = academic["year"]
    profile.semester = academic["semester"]
    profile.student_number = "2150094"
    profile.verification_status = StudentVerificationStatus.APPROVED
    profile.save()
    return profile


def import_production_settings(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "phase-3-production-secret")
    monkeypatch.setenv("DEBUG", "False")
    monkeypatch.setenv("ALLOWED_HOSTS", "api.example.com")
    monkeypatch.setenv("CSRF_TRUSTED_ORIGINS", "https://api.example.com")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://dashboard.example.com")
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pass@localhost:5432/panorama")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setattr("config.settings.env.config", lambda name: (_ for _ in ()).throw(UndefinedValueError(name)))
    sys.modules.pop("config.settings.production", None)
    return importlib.import_module("config.settings.production")


def test_production_uses_redis_cache_and_channel_layer(monkeypatch):
    production = import_production_settings(monkeypatch)

    assert production.CACHES["default"]["BACKEND"] == "django.core.cache.backends.redis.RedisCache"
    assert production.CACHES["default"]["LOCATION"] == "redis://localhost:6379/0"
    assert production.CHANNEL_LAYERS["default"]["BACKEND"] == "channels_redis.core.RedisChannelLayer"
    assert production.CHANNEL_LAYERS["default"]["CONFIG"]["hosts"] == ["redis://localhost:6379/0"]


def test_testing_settings_remain_in_memory():
    from django.conf import settings

    assert settings.CACHES["default"]["BACKEND"] == "django.core.cache.backends.locmem.LocMemCache"
    assert settings.CHANNEL_LAYERS["default"]["BACKEND"] == "channels.layers.InMemoryChannelLayer"


@pytest.mark.django_db
def test_makemigrations_check_has_no_pending_model_changes():
    call_command("makemigrations", check=True, dry_run=True, verbosity=0)


@pytest.mark.django_db
def test_readiness_endpoint_uses_response_envelope(api_client):
    response = api_client.get("/api/v1/health/ready/")

    assert response.status_code == status.HTTP_200_OK
    assert response.data["success"] is True
    assert response.data["data"]["database"] == "healthy"
    assert response.data["data"]["cache"] == "healthy"


@pytest.mark.django_db
def test_duplicate_group_membership_is_rejected_safely(api_client, student_user, admin_user, academic):
    approve_student(student_user, academic)
    group = Group.objects.create(name="Reliability Group", university=academic["university"], created_by=admin_user)
    auth(api_client, student_user)

    first = api_client.post(f"/api/v1/groups/{group.id}/join/")
    second = api_client.post(f"/api/v1/groups/{group.id}/join/")

    assert first.status_code == status.HTTP_200_OK
    assert second.status_code == status.HTTP_400_BAD_REQUEST
    assert GroupMembership.objects.filter(group=group, user=student_user).count() == 1


@pytest.mark.django_db
def test_duplicate_device_token_updates_existing_row(api_client, normal_user, admin_user):
    auth(api_client, normal_user)
    first = api_client.post("/api/v1/notifications/device-tokens/", {"token": "same-token", "platform": "android"}, format="json")
    auth(api_client, admin_user)
    second = api_client.post("/api/v1/notifications/device-tokens/", {"token": "same-token", "platform": "ios"}, format="json")

    assert first.status_code == status.HTTP_201_CREATED
    assert second.status_code == status.HTTP_201_CREATED
    assert DeviceToken.objects.filter(token="same-token").count() == 1
    token = DeviceToken.objects.get(token="same-token")
    assert token.user == admin_user
    assert token.platform == "ios"


@pytest.mark.django_db
def test_verification_review_is_single_transition(api_client, student_user, admin_user, academic):
    profile = student_user.student_profile
    verification = VerificationRequest.objects.create(
        user=student_user,
        student_profile=profile,
        university=academic["university"],
        faculty=academic["faculty"],
        major=academic["major"],
        academic_year=academic["year"],
        semester=academic["semester"],
        student_number="2150094",
        card_image="verification_cards/card.gif",
        status=VerificationStatus.PENDING,
    )
    auth(api_client, admin_user)

    approved = api_client.post(f"/api/v1/dashboard/verifications/{verification.id}/approve/", {}, format="json")
    rejected_again = api_client.post(f"/api/v1/dashboard/verifications/{verification.id}/reject/", {}, format="json")

    assert approved.status_code == status.HTTP_200_OK
    assert rejected_again.status_code == status.HTTP_400_BAD_REQUEST
    verification.refresh_from_db()
    assert verification.status == VerificationStatus.APPROVED


@pytest.mark.django_db
def test_print_status_transition_is_single_and_records_history(api_client, normal_user, print_staff):
    order = PrintOrder.objects.create(user=normal_user)
    auth(api_client, print_staff)

    valid = api_client.patch(f"/api/v1/dashboard/printing/orders/{order.id}/status/", {"status": PrintOrderStatus.UNDER_REVIEW}, format="json")
    invalid_repeat = api_client.patch(f"/api/v1/dashboard/printing/orders/{order.id}/status/", {"status": PrintOrderStatus.UNDER_REVIEW}, format="json")

    assert valid.status_code == status.HTTP_200_OK
    assert invalid_repeat.status_code == status.HTTP_400_BAD_REQUEST
    assert PrintOrderStatusHistory.objects.filter(order=order, new_status=PrintOrderStatus.UNDER_REVIEW).count() == 1


@pytest.mark.django_db
def test_closed_support_ticket_rejects_new_messages(api_client, normal_user):
    ticket = SupportTicket.objects.create(user=normal_user, category="technical", subject="Closed", status=SupportTicketStatus.CLOSED)
    auth(api_client, normal_user)

    response = api_client.post(f"/api/v1/support/tickets/{ticket.id}/messages/", {"message": "hello"}, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_dashboard_files_are_paginated_and_filterable(api_client, admin_user):
    FileResource.objects.create(
        title="Public A",
        file=SimpleUploadedFile("a.pdf", b"%PDF-1.4 a", content_type="application/pdf"),
        uploaded_by=admin_user,
        visibility=FileVisibility.PUBLIC,
    )
    FileResource.objects.create(
        title="Admin B",
        file=SimpleUploadedFile("b.pdf", b"%PDF-1.4 b", content_type="application/pdf"),
        uploaded_by=admin_user,
        visibility=FileVisibility.ADMIN_ONLY,
    )
    auth(api_client, admin_user)

    response = api_client.get("/api/v1/dashboard/files/?visibility=public&page_size=1")

    assert response.status_code == status.HTTP_200_OK
    assert response.data["data"]["count"] == 1
    assert response.data["data"]["results"][0]["visibility"] == FileVisibility.PUBLIC


@pytest.mark.django_db
def test_cleanup_expired_otp_command_is_idempotent(normal_user):
    used = OTPCode.objects.create(
        user=normal_user,
        phone_number=normal_user.phone_number,
        purpose="verify_phone",
        expires_at=timezone.now() - timezone.timedelta(days=3),
        is_used=True,
    )
    used.set_code("111111")
    used.save()
    active = OTPCode.objects.create(
        user=normal_user,
        phone_number=normal_user.phone_number,
        purpose="verify_phone",
        expires_at=timezone.now() + timezone.timedelta(minutes=10),
    )
    active.set_code("222222")
    active.save()
    OTPCode.objects.filter(pk=used.pk).update(created_at=timezone.now() - timezone.timedelta(days=3))

    call_command("cleanup_expired_otp", retention_days=1)
    call_command("cleanup_expired_otp", retention_days=1)

    assert not OTPCode.objects.filter(pk=used.pk).exists()
    assert OTPCode.objects.filter(pk=active.pk).exists()
