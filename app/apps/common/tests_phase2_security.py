from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
import pytest
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from apps.accounts.choices import OTPPurpose, StudentVerificationStatus, UserRole
from apps.accounts.models import OTPCode, StudentProfile, User
from apps.accounts.services import OTPService
from apps.audit.models import AuditLog
from apps.files.models import FileResource, FileVisibility
from apps.groups.models import Group, GroupMembership, GroupMembershipRole, GroupMembershipStatus
from apps.printing.models import PrintOrder
from apps.support.models import SupportTicket
from apps.universities.models import AcademicYear, Faculty, Major, Semester, University


GIF_BYTES = b"GIF87a\x01\x00\x01\x00\x80\x01\x00\x00\x00\x00ccc,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(
        full_name="Admin",
        email="security-admin@example.com",
        phone_number="+963966100001",
        password="StrongPass123!",
        role=UserRole.ADMIN,
    )


@pytest.fixture
def print_staff(db):
    return User.objects.create_user(
        full_name="Print Staff",
        email="security-print@example.com",
        phone_number="+963966100002",
        password="StrongPass123!",
        role=UserRole.PRINT_STAFF,
    )


@pytest.fixture
def normal_user(db):
    return User.objects.create_user(
        full_name="Normal",
        email="security-normal@example.com",
        phone_number="+963966100003",
        password="StrongPass123!",
        role=UserRole.NORMAL_USER,
    )


@pytest.fixture
def student_user(db):
    user = User.objects.create_user(
        full_name="Student",
        email="security-student@example.com",
        phone_number="+963966100004",
        password="StrongPass123!",
        role=UserRole.STUDENT,
    )
    StudentProfile.objects.create(user=user)
    return user


@pytest.fixture
def academic(db):
    university = University.objects.create(name="Security University", code="SU")
    faculty = Faculty.objects.create(university=university, name="Security Faculty", code="2")
    major = Major.objects.create(faculty=faculty, name="Security Major", code="SM")
    year = AcademicYear.objects.create(name="First", order=1)
    semester = Semester.objects.create(name="First", order=1)
    return {"university": university, "faculty": faculty, "major": major, "year": year, "semester": semester}


def auth(client, user):
    client.force_authenticate(user=user)


def upload(name="file.pdf", content=b"%PDF-1.4 test", content_type="application/pdf"):
    return SimpleUploadedFile(name, content, content_type=content_type)


def image_upload(name="image.gif", content=GIF_BYTES):
    return SimpleUploadedFile(name, content, content_type="image/gif")


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
    user.is_phone_verified = True
    user.save(update_fields=["is_phone_verified", "updated_at"])
    return profile


@pytest.mark.django_db
def test_otp_success_reuse_expiry_and_max_attempts(api_client, normal_user, settings):
    send = api_client.post(
        "/api/v1/auth/otp/send/",
        {"phone_number": normal_user.phone_number, "purpose": OTPPurpose.VERIFY_PHONE},
        format="json",
    )
    code = send.data["data"]["development_otp"]
    otp = OTPCode.objects.get(phone_number=normal_user.phone_number, purpose=OTPPurpose.VERIFY_PHONE)
    assert otp.code_hash != code

    verified = api_client.post(
        "/api/v1/auth/otp/verify/",
        {"phone_number": normal_user.phone_number, "purpose": OTPPurpose.VERIFY_PHONE, "code": code},
        format="json",
    )
    assert verified.status_code == status.HTTP_200_OK

    reused = api_client.post(
        "/api/v1/auth/otp/verify/",
        {"phone_number": normal_user.phone_number, "purpose": OTPPurpose.VERIFY_PHONE, "code": code},
        format="json",
    )
    assert reused.status_code == status.HTTP_400_BAD_REQUEST

    expired_otp, expired_code = OTPCode.objects.create(
        user=normal_user,
        phone_number=normal_user.phone_number,
        purpose=OTPPurpose.RESET_PASSWORD,
        expires_at=timezone.now() - timezone.timedelta(minutes=1),
    ), "123456"
    expired_otp.set_code(expired_code)
    expired_otp.save()
    expired = api_client.post(
        "/api/v1/auth/confirm-password-reset/",
        {
            "phone_number": normal_user.phone_number,
            "code": expired_code,
            "new_password": "ResetStrongPass123!",
            "new_password_confirm": "ResetStrongPass123!",
        },
        format="json",
    )
    assert expired.status_code == status.HTTP_400_BAD_REQUEST

    settings.MAX_OTP_VERIFY_ATTEMPTS = 2
    limited, limited_code = OTPCode.objects.create(
        user=normal_user,
        phone_number=normal_user.phone_number,
        purpose=OTPPurpose.RESET_PASSWORD,
        expires_at=timezone.now() + timezone.timedelta(minutes=10),
    ), "654321"
    limited.set_code(limited_code)
    limited.save()
    for _ in range(2):
        with pytest.raises(Exception):
            OTPService.verify_otp(normal_user.phone_number, "000000", OTPPurpose.RESET_PASSWORD)
    with pytest.raises(Exception):
        OTPService.verify_otp(normal_user.phone_number, limited_code, OTPPurpose.RESET_PASSWORD)
    limited.refresh_from_db()
    assert limited.attempts_count == 2
    assert AuditLog.objects.filter(action="otp_verification_failed").exists()


@pytest.mark.django_db
def test_password_reset_request_is_enumeration_safe(api_client, normal_user):
    existing = api_client.post("/api/v1/auth/request-password-reset/", {"phone_number": normal_user.phone_number}, format="json")
    assert existing.status_code == status.HTTP_200_OK
    assert OTPCode.objects.filter(phone_number=normal_user.phone_number, purpose=OTPPurpose.RESET_PASSWORD).exists()

    unknown_phone = "+963966199999"
    unknown = api_client.post("/api/v1/auth/request-password-reset/", {"phone_number": unknown_phone}, format="json")
    assert unknown.status_code == status.HTTP_200_OK
    assert unknown.data["success"] is True
    assert not OTPCode.objects.filter(phone_number=unknown_phone, purpose=OTPPurpose.RESET_PASSWORD).exists()

    invalid = api_client.post("/api/v1/auth/request-password-reset/", {"phone_number": "abc"}, format="json")
    assert invalid.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_login_throttle_returns_unified_envelope(api_client, normal_user, monkeypatch):
    cache.clear()
    monkeypatch.setitem(ScopedRateThrottle.THROTTLE_RATES, "auth_login", "1/minute")
    first = api_client.post("/api/v1/auth/login/", {"identifier": normal_user.email, "password": "bad"}, format="json")
    second = api_client.post("/api/v1/auth/login/", {"identifier": normal_user.email, "password": "bad"}, format="json")

    assert first.status_code == status.HTTP_400_BAD_REQUEST
    assert second.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert second.data["success"] is False
    assert "errors" in second.data


@pytest.mark.django_db
def test_upload_validation_for_images_and_documents(api_client, admin_user, academic):
    auth(api_client, admin_user)
    valid_group = api_client.post(
        "/api/v1/dashboard/groups/",
        {"name": "Image Group", "university": academic["university"].id, "image": image_upload()},
        format="multipart",
    )
    assert valid_group.status_code == status.HTTP_201_CREATED

    invalid_image = api_client.post(
        "/api/v1/dashboard/groups/",
        {"name": "Bad Image", "image": SimpleUploadedFile("bad.exe", GIF_BYTES, content_type="image/gif")},
        format="multipart",
    )
    assert invalid_image.status_code == status.HTTP_400_BAD_REQUEST

    valid_file = api_client.post(
        "/api/v1/dashboard/files/",
        {"title": "Valid PDF", "file": upload(), "visibility": FileVisibility.PUBLIC},
        format="multipart",
    )
    assert valid_file.status_code == status.HTTP_201_CREATED

    invalid_file = api_client.post(
        "/api/v1/dashboard/files/",
        {"title": "Bad File", "file": upload("bad.exe"), "visibility": FileVisibility.PUBLIC},
        format="multipart",
    )
    assert invalid_file.status_code == status.HTTP_400_BAD_REQUEST

    empty_file = api_client.post(
        "/api/v1/dashboard/files/",
        {"title": "Empty File", "file": upload("empty.pdf", b""), "visibility": FileVisibility.PUBLIC},
        format="multipart",
    )
    assert empty_file.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_protected_file_view_enforces_visibility(api_client, admin_user, normal_user):
    public_file = FileResource.objects.create(title="Public", file=upload(), uploaded_by=admin_user, visibility=FileVisibility.PUBLIC)
    admin_file = FileResource.objects.create(title="Admin", file=upload("admin.pdf"), uploaded_by=admin_user, visibility=FileVisibility.ADMIN_ONLY)

    auth(api_client, normal_user)
    allowed = api_client.get(f"/api/v1/files/{public_file.id}/view/")
    denied = api_client.get(f"/api/v1/files/{admin_file.id}/view/")

    assert allowed.status_code == status.HTTP_200_OK
    assert denied.status_code == status.HTTP_403_FORBIDDEN
    assert AuditLog.objects.filter(action="file_accessed", target_id=str(public_file.id)).exists()


@pytest.mark.django_db
def test_chat_cross_group_reply_and_blocked_send_rejected(api_client, admin_user, student_user, academic):
    approve_student(student_user, academic)
    group = Group.objects.create(name="Group One", university=academic["university"], created_by=admin_user)
    other_group = Group.objects.create(name="Group Two", university=academic["university"], created_by=admin_user)
    GroupMembership.objects.create(group=group, user=student_user, status=GroupMembershipStatus.APPROVED)
    GroupMembership.objects.create(group=other_group, user=student_user, status=GroupMembershipStatus.APPROVED)
    auth(api_client, student_user)

    first = api_client.post(f"/api/v1/groups/{other_group.id}/messages/", {"content": "Other"}, format="json")
    cross_reply = api_client.post(
        f"/api/v1/groups/{group.id}/messages/",
        {"content": "Reply", "reply_to": first.data["data"]["id"]},
        format="json",
    )
    assert cross_reply.status_code == status.HTTP_400_BAD_REQUEST

    blocked_group = Group.objects.create(name="Blocked", university=academic["university"], created_by=admin_user)
    GroupMembership.objects.create(group=blocked_group, user=student_user, status=GroupMembershipStatus.BLOCKED)
    blocked = api_client.post(f"/api/v1/groups/{blocked_group.id}/messages/", {"content": "No"}, format="json")
    assert blocked.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_moderator_can_delete_and_admins_only_group_blocks_member(api_client, admin_user, student_user, academic):
    approve_student(student_user, academic)
    group = Group.objects.create(
        name="Admins Only",
        university=academic["university"],
        created_by=admin_user,
        send_messages_permission="admins_only",
    )
    membership = GroupMembership.objects.create(group=group, user=student_user, status=GroupMembershipStatus.APPROVED)
    auth(api_client, student_user)
    blocked = api_client.post(f"/api/v1/groups/{group.id}/messages/", {"content": "No"}, format="json")
    assert blocked.status_code == status.HTTP_403_FORBIDDEN

    membership.role = GroupMembershipRole.MODERATOR
    membership.save(update_fields=["role", "updated_at"])
    created = api_client.post(f"/api/v1/groups/{group.id}/messages/", {"content": "Allowed"}, format="json")
    assert created.status_code == status.HTTP_201_CREATED
    deleted = api_client.delete(f"/api/v1/groups/{group.id}/messages/{created.data['data']['id']}/")
    assert deleted.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_print_and_support_assignment_role_validation(api_client, admin_user, print_staff, normal_user, student_user):
    order = PrintOrder.objects.create(user=normal_user)
    ticket = SupportTicket.objects.create(user=normal_user, category="technical", subject="Issue")
    auth(api_client, admin_user)

    print_invalid = api_client.patch(f"/api/v1/dashboard/printing/orders/{order.id}/assign/", {"assigned_to": normal_user.id}, format="json")
    print_valid = api_client.patch(f"/api/v1/dashboard/printing/orders/{order.id}/assign/", {"assigned_to": print_staff.id}, format="json")
    support_invalid = api_client.post(f"/api/v1/dashboard/support/tickets/{ticket.id}/assign/", {"assigned_to": student_user.id}, format="json")
    support_valid = api_client.post(f"/api/v1/dashboard/support/tickets/{ticket.id}/assign/", {"assigned_to": admin_user.id}, format="json")

    assert print_invalid.status_code == status.HTTP_400_BAD_REQUEST
    assert print_valid.status_code == status.HTTP_200_OK
    assert support_invalid.status_code == status.HTTP_400_BAD_REQUEST
    assert support_valid.status_code == status.HTTP_200_OK
    assert AuditLog.objects.filter(action="print_order_assigned", target_id=str(order.id)).exists()
    assert AuditLog.objects.filter(action="support_ticket_assigned", target_id=str(ticket.id)).exists()


def test_production_settings_disable_development_otp_and_set_security_headers(monkeypatch):
    import importlib
    import sys

    from decouple import UndefinedValueError

    monkeypatch.setenv("SECRET_KEY", "test-secret-for-phase-2-production-settings")
    monkeypatch.setenv("ALLOWED_HOSTS", "api.example.com")
    monkeypatch.setenv("CSRF_TRUSTED_ORIGINS", "https://api.example.com")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://dashboard.example.com")
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pass@localhost:5432/panorama")
    monkeypatch.setattr("config.settings.env.config", lambda name: (_ for _ in ()).throw(UndefinedValueError(name)))

    sys.modules.pop("config.settings.production", None)
    production = importlib.import_module("config.settings.production")

    assert production.RETURN_DEVELOPMENT_OTP is False
    assert production.SECURE_CONTENT_TYPE_NOSNIFF is True
    assert production.SECURE_REFERRER_POLICY == "same-origin"
    assert production.X_FRAME_OPTIONS == "DENY"
