import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.choices import OTPPurpose, StudentAccountRequestStatus, StudentVerificationStatus, UserRole
from apps.accounts.models import StudentAccountRequest, StudentProfile, User
from apps.universities.models import Faculty, Major, University


GIF_BYTES = b"GIF87a\x01\x00\x01\x00\x80\x01\x00\x00\x00\x00ccc,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(
        full_name="Admin User",
        email="admin-sar@example.com",
        phone_number="+963911111101",
        password="StrongPass123!",
        role=UserRole.ADMIN,
    )


@pytest.fixture
def it_support_user(db):
    return User.objects.create_user(
        full_name="IT Support",
        email="it-sar@example.com",
        phone_number="+963911111102",
        password="StrongPass123!",
        role=UserRole.IT_SUPPORT,
        is_staff=True,
    )


@pytest.fixture
def normal_user(db):
    return User.objects.create_user(
        full_name="Normal User",
        email="normal-sar@example.com",
        phone_number="+963900000014",
        password="StrongPass123!",
        role=UserRole.NORMAL_USER,
    )


@pytest.fixture
def print_staff_user(db):
    return User.objects.create_user(
        full_name="Print Staff",
        email="print-sar@example.com",
        phone_number="+963911111103",
        password="StrongPass123!",
        role=UserRole.PRINT_STAFF,
    )


@pytest.fixture
def academic_structure(db):
    university = University.objects.create(name="Damascus University SAR", code="DUSAR")
    faculty = Faculty.objects.create(university=university, name="Engineering SAR", code="4")
    major = Major.objects.create(faculty=faculty, name="Software SAR", code="SWSAR")
    return {"university": university, "faculty": faculty, "major": major}


def student_request_payload(academic_structure, **overrides):
    payload = {
        "full_name": "New Student",
        "email": "newstudent@example.com",
        "phone_number": "+963900000010",
        "university": academic_structure["university"].id,
        "faculty": academic_structure["faculty"].id,
        "major": academic_structure["major"].id,
        "student_number": "41234567",
        "password": "StrongPass123!",
        "password_confirm": "StrongPass123!",
        "uploaded_card": SimpleUploadedFile("card.gif", GIF_BYTES, content_type="image/gif"),
    }
    payload.update(overrides)
    return payload


def create_student_request(api_client, academic_structure, **overrides):
    payload = student_request_payload(academic_structure, **overrides)
    return api_client.post(reverse("student-account-request-create"), payload, format="multipart")


@pytest.mark.django_db
def test_normal_user_registration_sends_otp_and_requires_verification(api_client):
    payload = {
        "full_name": "Ahmad Ali",
        "email": "ahmad-sar@example.com",
        "phone_number": "+963900000099",
        "password": "StrongPass123!",
        "password_confirm": "StrongPass123!",
    }
    response = api_client.post(reverse("register-normal"), payload, format="json")

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["success"] is True
    assert "رمز تحقق" in response.data["message"]
    assert response.data["data"]["requires_otp"] is True
    assert response.data["data"]["otp_purpose"] == OTPPurpose.VERIFY_PHONE
    assert response.data["data"]["next_step"] == "verify_phone"
    assert "development_otp" in response.data["data"]


@pytest.mark.django_db
def test_verify_phone_alias_endpoint(api_client):
    payload = {
        "full_name": "Verify Phone User",
        "email": "verifyphone@example.com",
        "phone_number": "+963900000098",
        "password": "StrongPass123!",
        "password_confirm": "StrongPass123!",
    }
    register_response = api_client.post(reverse("register-normal"), payload, format="json")
    raw_otp = register_response.data["data"]["development_otp"]

    verify_response = api_client.post(
        reverse("verify-phone"),
        {"phone_number": payload["phone_number"], "code": raw_otp},
        format="json",
    )
    assert verify_response.status_code == status.HTTP_200_OK
    assert verify_response.data["data"]["is_phone_verified"] is True
    user = User.objects.get(email=payload["email"])
    assert user.is_phone_verified is True


@pytest.mark.django_db
def test_student_account_request_create_success(api_client, academic_structure):
    response = create_student_request(api_client, academic_structure)

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["success"] is True
    assert response.data["data"]["status"] == StudentAccountRequestStatus.PENDING_REVIEW
    assert response.data["data"]["next_step"] == "admin_review"
    request_obj = StudentAccountRequest.objects.get(email="newstudent@example.com")
    assert request_obj.otp_hash == ""
    assert not User.objects.filter(email="newstudent@example.com").exists()


@pytest.mark.django_db
def test_student_account_request_missing_card_fails(api_client, academic_structure):
    payload = student_request_payload(academic_structure)
    payload.pop("uploaded_card")
    response = api_client.post(reverse("student-account-request-create"), payload, format="multipart")
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_student_account_request_password_mismatch_fails(api_client, academic_structure):
    response = create_student_request(
        api_client,
        academic_structure,
        password_confirm="DifferentPass123!",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_student_account_request_duplicate_email_fails(api_client, academic_structure, admin_user):
    create_student_request(api_client, academic_structure)
    response = create_student_request(
        api_client,
        academic_structure,
        email=admin_user.email,
        phone_number="+963900000011",
        student_number="41234568",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_student_cannot_login_before_activation(api_client, academic_structure):
    payload = student_request_payload(academic_structure)
    create_student_request(api_client, academic_structure)
    login_response = api_client.post(
        reverse("login"),
        {"identifier": payload["email"], "password": payload["password"]},
        format="json",
    )
    assert login_response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_dashboard_list_allowed_for_admin_and_print_staff_forbidden(api_client, admin_user, print_staff_user, academic_structure):
    create_student_request(api_client, academic_structure)

    api_client.force_authenticate(admin_user)
    list_response = api_client.get("/api/v1/dashboard/student-account-requests/")
    assert list_response.status_code == status.HTTP_200_OK

    api_client.force_authenticate(print_staff_user)
    forbidden_response = api_client.get("/api/v1/dashboard/student-account-requests/")
    assert forbidden_response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_dashboard_approve_returns_otp_only_to_dashboard(api_client, admin_user, academic_structure):
    create_response = create_student_request(api_client, academic_structure)
    request_id = create_response.data["data"]["request_id"]
    request_obj = StudentAccountRequest.objects.get(public_id=request_id)

    api_client.force_authenticate(admin_user)
    approve_response = api_client.post(
        f"/api/v1/dashboard/student-account-requests/{request_obj.id}/approve/",
        {},
        format="json",
    )
    assert approve_response.status_code == status.HTTP_200_OK
    assert "otp_code" in approve_response.data["data"]
    assert len(approve_response.data["data"]["otp_code"]) == 6
    assert "manual_whatsapp_message" in approve_response.data["data"]

    status_response = api_client.get(
        reverse("student-account-request-status", kwargs={"public_id": request_id}),
    )
    assert "otp_code" not in status_response.data["data"]
    assert status_response.data["data"]["can_enter_otp"] is True


@pytest.mark.django_db
def test_dashboard_reject_requires_reason(api_client, admin_user, academic_structure):
    create_response = create_student_request(api_client, academic_structure)
    request_obj = StudentAccountRequest.objects.get(public_id=create_response.data["data"]["request_id"])

    api_client.force_authenticate(admin_user)
    response = api_client.post(
        f"/api/v1/dashboard/student-account-requests/{request_obj.id}/reject/",
        {},
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST

    response = api_client.post(
        f"/api/v1/dashboard/student-account-requests/{request_obj.id}/reject/",
        {"rejection_reason": "Invalid card"},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    request_obj.refresh_from_db()
    assert request_obj.status == StudentAccountRequestStatus.REJECTED


@pytest.mark.django_db
def test_dashboard_needs_update_requires_reason(api_client, admin_user, academic_structure):
    create_response = create_student_request(api_client, academic_structure)
    request_obj = StudentAccountRequest.objects.get(public_id=create_response.data["data"]["request_id"])

    api_client.force_authenticate(admin_user)
    response = api_client.post(
        f"/api/v1/dashboard/student-account-requests/{request_obj.id}/needs-update/",
        {"needs_update_reason": "Please upload a clearer card image"},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    request_obj.refresh_from_db()
    assert request_obj.status == StudentAccountRequestStatus.NEEDS_UPDATE


@pytest.mark.django_db
def test_resend_otp_respects_cooldown(api_client, admin_user, academic_structure):
    create_response = create_student_request(api_client, academic_structure)
    request_obj = StudentAccountRequest.objects.get(public_id=create_response.data["data"]["request_id"])

    api_client.force_authenticate(admin_user)
    api_client.post(f"/api/v1/dashboard/student-account-requests/{request_obj.id}/approve/", {}, format="json")
    first_resend = api_client.post(
        f"/api/v1/dashboard/student-account-requests/{request_obj.id}/resend-otp/",
        {},
        format="json",
    )
    assert first_resend.status_code == status.HTTP_200_OK
    second_resend = api_client.post(
        f"/api/v1/dashboard/student-account-requests/{request_obj.id}/resend-otp/",
        {},
        format="json",
    )
    assert second_resend.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_student_otp_verify_activates_account_and_allows_login(api_client, admin_user, academic_structure):
    payload = student_request_payload(academic_structure)
    create_response = create_student_request(api_client, academic_structure)
    request_id = create_response.data["data"]["request_id"]
    request_obj = StudentAccountRequest.objects.get(public_id=request_id)

    api_client.force_authenticate(admin_user)
    approve_response = api_client.post(
        f"/api/v1/dashboard/student-account-requests/{request_obj.id}/approve/",
        {},
        format="json",
    )
    otp_code = approve_response.data["data"]["otp_code"]

    verify_response = api_client.post(
        reverse("student-account-request-verify-otp", kwargs={"public_id": request_id}),
        {"code": otp_code},
        format="json",
    )
    assert verify_response.status_code == status.HTTP_200_OK
    assert verify_response.data["data"]["status"] == StudentAccountRequestStatus.ACTIVE
    assert "otp_code" not in verify_response.data["data"]

    user = User.objects.get(email=payload["email"])
    assert user.role == UserRole.STUDENT
    assert user.is_phone_verified is True
    profile = StudentProfile.objects.get(user=user)
    assert profile.verification_status == StudentVerificationStatus.APPROVED

    login_response = api_client.post(
        reverse("login"),
        {"identifier": payload["email"], "password": payload["password"]},
        format="json",
    )
    assert login_response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_wrong_student_otp_increments_attempts(api_client, admin_user, academic_structure):
    create_response = create_student_request(api_client, academic_structure)
    request_id = create_response.data["data"]["request_id"]
    request_obj = StudentAccountRequest.objects.get(public_id=request_id)

    api_client.force_authenticate(admin_user)
    api_client.post(f"/api/v1/dashboard/student-account-requests/{request_obj.id}/approve/", {}, format="json")

    verify_response = api_client.post(
        reverse("student-account-request-verify-otp", kwargs={"public_id": request_id}),
        {"code": "000000"},
        format="json",
    )
    assert verify_response.status_code == status.HTTP_400_BAD_REQUEST
    request_obj.refresh_from_db()
    assert request_obj.otp_attempt_count == 1


@pytest.mark.django_db
def test_expired_student_otp_fails(api_client, admin_user, academic_structure):
    create_response = create_student_request(api_client, academic_structure)
    request_id = create_response.data["data"]["request_id"]
    request_obj = StudentAccountRequest.objects.get(public_id=request_id)

    api_client.force_authenticate(admin_user)
    approve_response = api_client.post(
        f"/api/v1/dashboard/student-account-requests/{request_obj.id}/approve/",
        {},
        format="json",
    )
    otp_code = approve_response.data["data"]["otp_code"]
    request_obj.otp_expires_at = timezone.now() - timezone.timedelta(minutes=1)
    request_obj.save(update_fields=["otp_expires_at", "updated_at"])

    verify_response = api_client.post(
        reverse("student-account-request-verify-otp", kwargs={"public_id": request_id}),
        {"code": otp_code},
        format="json",
    )
    assert verify_response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_status_can_enter_otp_false_before_approval(api_client, academic_structure):
    create_response = create_student_request(api_client, academic_structure)
    request_id = create_response.data["data"]["request_id"]

    status_response = api_client.get(
        reverse("student-account-request-status", kwargs={"public_id": request_id}),
    )
    assert status_response.data["data"]["can_enter_otp"] is False
    assert status_response.data["data"]["can_resubmit"] is False


@pytest.mark.django_db
def test_status_returns_rejection_and_needs_update_reasons(api_client, admin_user, academic_structure):
    create_response = create_student_request(api_client, academic_structure)
    request_obj = StudentAccountRequest.objects.get(public_id=create_response.data["data"]["request_id"])

    api_client.force_authenticate(admin_user)
    api_client.post(
        f"/api/v1/dashboard/student-account-requests/{request_obj.id}/reject/",
        {"rejection_reason": "Invalid card"},
        format="json",
    )
    status_response = api_client.get(
        reverse("student-account-request-status", kwargs={"public_id": request_obj.public_id}),
    )
    assert status_response.data["data"]["rejection_reason"] == "Invalid card"
    assert "otp_code" not in status_response.data["data"]

    create_response = create_student_request(
        api_client,
        academic_structure,
        email="needsupdate@example.com",
        phone_number="+963900000012",
        student_number="41234568",
    )
    request_obj = StudentAccountRequest.objects.get(public_id=create_response.data["data"]["request_id"])
    api_client.post(
        f"/api/v1/dashboard/student-account-requests/{request_obj.id}/needs-update/",
        {"needs_update_reason": "Upload clearer card"},
        format="json",
    )
    status_response = api_client.get(
        reverse("student-account-request-status", kwargs={"public_id": request_obj.public_id}),
    )
    assert status_response.data["data"]["needs_update_reason"] == "Upload clearer card"


@pytest.mark.django_db
def test_it_support_can_approve_and_get_otp(api_client, it_support_user, academic_structure):
    create_response = create_student_request(api_client, academic_structure)
    request_obj = StudentAccountRequest.objects.get(public_id=create_response.data["data"]["request_id"])

    api_client.force_authenticate(it_support_user)
    response = api_client.post(
        f"/api/v1/dashboard/student-account-requests/{request_obj.id}/approve/",
        {},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    assert "otp_code" in response.data["data"]
    assert response.data["data"]["resend_after_seconds"] == 60


@pytest.mark.django_db
@pytest.mark.parametrize(
    "endpoint_suffix,payload",
    [
        ("approve/", {}),
        ("reject/", {"rejection_reason": "No"}),
        ("needs-update/", {"needs_update_reason": "Fix"}),
        ("resend-otp/", {}),
        ("card-preview-token/", {}),
    ],
)
def test_print_staff_forbidden_on_dashboard_actions(
    api_client, admin_user, print_staff_user, academic_structure, endpoint_suffix, payload
):
    create_response = create_student_request(api_client, academic_structure)
    request_obj = StudentAccountRequest.objects.get(public_id=create_response.data["data"]["request_id"])
    if endpoint_suffix == "resend-otp/":
        api_client.force_authenticate(admin_user)
        api_client.post(
            f"/api/v1/dashboard/student-account-requests/{request_obj.id}/approve/",
            {},
            format="json",
        )

    api_client.force_authenticate(print_staff_user)
    response = api_client.post(
        f"/api/v1/dashboard/student-account-requests/{request_obj.id}/{endpoint_suffix}",
        payload,
        format="json",
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_dashboard_forbidden_for_unauthenticated_and_non_staff_users(api_client, normal_user, student_user, academic_structure):
    create_student_request(api_client, academic_structure)

    response = api_client.get("/api/v1/dashboard/student-account-requests/")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED

    api_client.force_authenticate(normal_user)
    assert api_client.get("/api/v1/dashboard/student-account-requests/").status_code == status.HTTP_403_FORBIDDEN

    api_client.force_authenticate(student_user)
    assert api_client.get("/api/v1/dashboard/student-account-requests/").status_code == status.HTTP_403_FORBIDDEN


@pytest.fixture
def student_user(db):
    user = User.objects.create_user(
        full_name="Student User",
        email="student-hardening@example.com",
        phone_number="+963900000013",
        password="StrongPass123!",
        role=UserRole.STUDENT,
    )
    return user


@pytest.mark.django_db
def test_old_student_otp_invalid_after_resend(api_client, admin_user, academic_structure):
    create_response = create_student_request(api_client, academic_structure)
    request_id = create_response.data["data"]["request_id"]
    request_obj = StudentAccountRequest.objects.get(public_id=request_id)

    api_client.force_authenticate(admin_user)
    approve_response = api_client.post(
        f"/api/v1/dashboard/student-account-requests/{request_obj.id}/approve/",
        {},
        format="json",
    )
    old_otp = approve_response.data["data"]["otp_code"]

    request_obj.otp_last_sent_at = timezone.now() - timezone.timedelta(seconds=61)
    request_obj.save(update_fields=["otp_last_sent_at", "updated_at"])
    resend_response = api_client.post(
        f"/api/v1/dashboard/student-account-requests/{request_obj.id}/resend-otp/",
        {},
        format="json",
    )
    assert resend_response.status_code == status.HTTP_200_OK
    new_otp = resend_response.data["data"]["otp_code"]
    assert new_otp != old_otp

    verify_old = api_client.post(
        reverse("student-account-request-verify-otp", kwargs={"public_id": request_id}),
        {"code": old_otp},
        format="json",
    )
    assert verify_old.status_code == status.HTTP_400_BAD_REQUEST

    verify_new = api_client.post(
        reverse("student-account-request-verify-otp", kwargs={"public_id": request_id}),
        {"code": new_otp},
        format="json",
    )
    assert verify_new.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_student_otp_max_attempts_fails(api_client, admin_user, academic_structure, settings):
    create_response = create_student_request(api_client, academic_structure)
    request_id = create_response.data["data"]["request_id"]
    request_obj = StudentAccountRequest.objects.get(public_id=request_id)

    api_client.force_authenticate(admin_user)
    api_client.post(f"/api/v1/dashboard/student-account-requests/{request_obj.id}/approve/", {}, format="json")

    for _ in range(settings.MAX_OTP_VERIFY_ATTEMPTS):
        api_client.post(
            reverse("student-account-request-verify-otp", kwargs={"public_id": request_id}),
            {"code": "000000"},
            format="json",
        )

    response = api_client.post(
        reverse("student-account-request-verify-otp", kwargs={"public_id": request_id}),
        {"code": "000000"},
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST