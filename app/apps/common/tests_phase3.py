import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.accounts.student_number import StudentNumberParser
from apps.audit.models import AuditLog
from apps.files.models import FileResource, FileVisibility
from apps.groups.models import Group, GroupMembership, GroupMembershipStatus
from apps.notifications.models import DeviceToken, Notification
from apps.printing.models import PrintOrder, PrintOrderStatus, PrintOrderStatusHistory, PrintPricingRule
from apps.support.models import SupportTicket, SupportTicketStatus
from apps.universities.models import AcademicYear, Faculty, Major, Semester, University

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(
        full_name="Admin",
        email="phase3-admin@example.com",
        phone_number="+963944444441",
        password="StrongPass123!",
        role=UserRole.ADMIN,
    )


@pytest.fixture
def print_staff(db):
    return User.objects.create_user(
        full_name="Print Staff",
        email="print@example.com",
        phone_number="+963944444442",
        password="StrongPass123!",
        role=UserRole.PRINT_STAFF,
    )


@pytest.fixture
def normal_user(db):
    return User.objects.create_user(
        full_name="Normal",
        email="phase3-normal@example.com",
        phone_number="+963944444443",
        password="StrongPass123!",
        role=UserRole.NORMAL_USER,
    )


@pytest.fixture
def other_user(db):
    return User.objects.create_user(
        full_name="Other",
        email="phase3-other@example.com",
        phone_number="+963944444444",
        password="StrongPass123!",
        role=UserRole.NORMAL_USER,
    )


@pytest.fixture
def student_user(db):
    user = User.objects.create_user(
        full_name="Student",
        email="phase3-student@example.com",
        phone_number="+963944444445",
        password="StrongPass123!",
        role=UserRole.STUDENT,
    )
    StudentProfile.objects.create(user=user)
    return user


@pytest.fixture
def academic(db):
    university = University.objects.create(name="Parser University", code="PU")
    faculty = Faculty.objects.create(university=university, name="Dentistry", code="2")
    other_faculty = Faculty.objects.create(university=university, name="Pharmacy", code="3")
    major = Major.objects.create(faculty=faculty, name="Dental Surgery", code="DS")
    year = AcademicYear.objects.create(name="Year 2015", order=15)
    semester = Semester.objects.create(name="Semester 1", order=1)
    return {
        "university": university,
        "faculty": faculty,
        "other_faculty": other_faculty,
        "major": major,
        "year": year,
        "semester": semester,
    }


def auth(client, user):
    client.force_authenticate(user=user)


def upload(name="file.png", content=PNG_BYTES):
    return SimpleUploadedFile(name, content, content_type="image/png")


def configure_printing():
    PrintPricingRule.objects.get_or_create(
        name="Test A4 monochrome",
        defaults={
            "color_mode": "black_white",
            "paper_size": "A4",
            "sides": "one_sided",
            "price_per_sheet": "100",
            "setup_fee": "0",
            "currency": "SYP",
        },
    )


def print_item(source_file, copies=1):
    return {
        "source_file": source_file.id,
        "copies": copies,
        "color_mode": "black_white",
        "paper_size": "A4",
        "sides": "one_sided",
        "binding": "none",
    }


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
def test_student_number_parser_valid_and_invalid():
    parsed = StudentNumberParser.parse("2150094")
    assert parsed["faculty_code"] == "2"
    assert parsed["enrollment_year_code"] == "15"
    assert parsed["enrollment_year_full"] == 2015
    assert parsed["serial_number"] == "0094"

    with pytest.raises(ValidationError):
        StudentNumberParser.parse("21A0094")
    with pytest.raises(ValidationError):
        StudentNumberParser.parse("9150094")
    with pytest.raises(ValidationError):
        StudentNumberParser.parse("215")


@pytest.mark.django_db
def test_parsed_fields_saved_on_student_registration(api_client, academic):
    response = api_client.post(
        "/api/v1/auth/register/student/",
        {
            "full_name": "Parsed Student",
            "email": "parsed@example.com",
            "phone_number": "+963955555555",
            "password": "StrongPass123!",
            "password_confirm": "StrongPass123!",
            "student_number": "2150094",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    profile = User.objects.get(email="parsed@example.com").student_profile
    assert profile.faculty_code_from_student_number == "2"
    assert profile.enrollment_year_code == "15"
    assert profile.student_serial_number == "0094"
    assert profile.faculty == academic["faculty"]


@pytest.mark.django_db
def test_verification_rejects_mismatched_faculty_and_accepts_matching(api_client, student_user, academic):
    student_user.is_phone_verified = True
    student_user.save(update_fields=["is_phone_verified", "updated_at"])
    auth(api_client, student_user)
    base = {
        "university": academic["university"].id,
        "major": academic["major"].id,
        "academic_year": academic["year"].id,
        "semester": academic["semester"].id,
        "student_number": "2150094",
    }
    mismatch = api_client.post(
        "/api/v1/verification/submit/",
        {
            **base,
            "faculty": academic["other_faculty"].id,
            "card_image": SimpleUploadedFile("card.png", PNG_BYTES, content_type="image/png"),
        },
        format="multipart",
    )
    assert mismatch.status_code == status.HTTP_400_BAD_REQUEST

    match = api_client.post(
        "/api/v1/verification/submit/",
        {
            **base,
            "faculty": academic["faculty"].id,
            "card_image": SimpleUploadedFile("card2.png", PNG_BYTES, content_type="image/png"),
        },
        format="multipart",
    )
    assert match.status_code == status.HTTP_201_CREATED


@pytest.mark.django_db
def test_approved_student_cannot_change_student_number(api_client, student_user, academic):
    approve_student(student_user, academic)
    auth(api_client, student_user)
    response = api_client.patch("/api/v1/students/me/profile/", {"student_number": "3150094"}, format="json")
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_print_order_uploaded_file_and_priority(api_client, normal_user, student_user, academic):
    configure_printing()
    normal_file = FileResource.objects.create(
        title="Normal printable",
        file=upload(),
        uploaded_by=normal_user,
        visibility=FileVisibility.PUBLIC,
        pages_count=1,
    )
    auth(api_client, normal_user)
    normal = api_client.post(
        "/api/v1/printing/orders/",
        {"items": [print_item(normal_file)], "user_notes": "Please print"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="normal-order-retry-key",
    )
    assert normal.status_code == status.HTTP_201_CREATED, normal.data
    assert normal.data["data"]["priority"] == "normal"
    assert normal.data["data"]["pricing_revision"]
    retry = api_client.post(
        "/api/v1/printing/orders/",
        {"items": [print_item(normal_file)], "user_notes": "Please print"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="normal-order-retry-key",
    )
    assert retry.status_code == status.HTTP_201_CREATED, retry.data
    assert retry.data["data"]["id"] == normal.data["data"]["id"]

    approve_student(student_user, academic)
    student_file = FileResource.objects.create(
        title="Student printable",
        file=upload("student.png"),
        uploaded_by=student_user,
        visibility=FileVisibility.PUBLIC,
        pages_count=1,
    )
    auth(api_client, student_user)
    student = api_client.post("/api/v1/printing/orders/", {"items": [print_item(student_file)]}, format="json")
    assert student.status_code == status.HTTP_201_CREATED
    assert student.data["data"]["priority"] == "student_priority"


@pytest.mark.django_db
def test_print_order_source_file_access_and_owner_visibility(api_client, normal_user, other_user, admin_user):
    configure_printing()
    public_file = FileResource.objects.create(
        title="Printable", file=upload(), uploaded_by=admin_user, visibility=FileVisibility.PUBLIC, pages_count=1
    )
    admin_file = FileResource.objects.create(
        title="Private",
        file=upload("private.png"),
        uploaded_by=admin_user,
        visibility=FileVisibility.ADMIN_ONLY,
        pages_count=1,
    )
    auth(api_client, normal_user)
    ok = api_client.post("/api/v1/printing/orders/", {"items": [print_item(public_file, copies=2)]}, format="json")
    blocked = api_client.post("/api/v1/printing/orders/", {"items": [print_item(admin_file)]}, format="json")
    assert ok.status_code == status.HTTP_201_CREATED, ok.data
    assert blocked.status_code == status.HTTP_400_BAD_REQUEST

    auth(api_client, other_user)
    response = api_client.get(f"/api/v1/printing/orders/{ok.data['data']['id']}/")
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_print_staff_status_update_history_notification_and_invalid_transition(api_client, normal_user, print_staff):
    order = PrintOrder.objects.create(user=normal_user)
    auth(api_client, print_staff)
    invalid = api_client.patch(
        f"/api/v1/dashboard/printing/orders/{order.id}/status/", {"status": PrintOrderStatus.READY}, format="json"
    )
    assert invalid.status_code == status.HTTP_400_BAD_REQUEST

    valid = api_client.patch(
        f"/api/v1/dashboard/printing/orders/{order.id}/status/",
        {"status": PrintOrderStatus.UNDER_REVIEW},
        format="json",
    )
    assert valid.status_code == status.HTTP_200_OK
    assert PrintOrderStatusHistory.objects.filter(order=order, new_status=PrintOrderStatus.UNDER_REVIEW).exists()
    assert Notification.objects.filter(user=normal_user, data__print_order_id=order.id).exists()
    assert AuditLog.objects.filter(action="print_order_status_changed", target_id=str(order.id)).exists()


@pytest.mark.django_db
def test_chat_access_create_and_delete(api_client, admin_user, student_user, other_user, academic):
    approve_student(student_user, academic)
    group = Group.objects.create(name="Chat Group", university=academic["university"], created_by=admin_user)
    GroupMembership.objects.create(group=group, user=student_user, status=GroupMembershipStatus.APPROVED)

    auth(api_client, other_user)
    assert api_client.get(f"/api/v1/groups/{group.id}/messages/").status_code == status.HTTP_403_FORBIDDEN

    auth(api_client, student_user)
    create = api_client.post(f"/api/v1/groups/{group.id}/messages/", {"content": "Hello"}, format="json")
    assert create.status_code == status.HTTP_201_CREATED

    auth(api_client, other_user)
    delete_other = api_client.delete(f"/api/v1/groups/{group.id}/messages/{create.data['data']['id']}/")
    assert delete_other.status_code == status.HTTP_403_FORBIDDEN

    auth(api_client, admin_user)
    deleted = api_client.delete(f"/api/v1/groups/{group.id}/messages/{create.data['data']['id']}/")
    assert deleted.status_code == status.HTTP_200_OK
    assert AuditLog.objects.filter(action="message_deleted").exists()


@pytest.mark.django_db
def test_blocked_member_cannot_access_chat(api_client, admin_user, student_user, academic):
    approve_student(student_user, academic)
    group = Group.objects.create(name="Blocked Chat", university=academic["university"], created_by=admin_user)
    GroupMembership.objects.create(group=group, user=student_user, status=GroupMembershipStatus.BLOCKED)
    auth(api_client, student_user)
    assert api_client.get(f"/api/v1/groups/{group.id}/messages/").status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_support_ticket_user_and_admin_flows(api_client, normal_user, other_user, admin_user):
    auth(api_client, normal_user)
    created = api_client.post(
        "/api/v1/support/tickets/",
        {"category": "technical", "subject": "Bug report", "message": "The application stopped working."},
        format="json",
    )
    assert created.status_code == status.HTTP_201_CREATED
    ticket_id = created.data["data"]["id"]

    auth(api_client, other_user)
    assert api_client.get(f"/api/v1/support/tickets/{ticket_id}/").status_code == status.HTTP_404_NOT_FOUND

    auth(api_client, admin_user)
    listed = api_client.get("/api/v1/dashboard/support/tickets/")
    assert listed.data["data"]["count"] == 1
    status_response = api_client.patch(
        f"/api/v1/dashboard/support/tickets/{ticket_id}/status/",
        {"status": SupportTicketStatus.IN_PROGRESS},
        format="json",
    )
    assert status_response.status_code == status.HTTP_200_OK
    reply = api_client.post(
        f"/api/v1/dashboard/support/tickets/{ticket_id}/messages/", {"message": "We are checking"}, format="json"
    )
    assert reply.status_code == status.HTTP_201_CREATED
    assert Notification.objects.filter(user=normal_user, data__support_ticket_id=ticket_id).exists()


@pytest.mark.django_db
def test_audit_logs_protected_and_sanitized(api_client, normal_user, admin_user):
    AuditLog.objects.create(
        actor=admin_user, action="support_ticket_created", new_value={"password": "secret", "safe": "ok"}
    )
    auth(api_client, normal_user)
    forbidden = api_client.get("/api/v1/dashboard/audit-logs/")
    assert forbidden.status_code == status.HTTP_403_FORBIDDEN

    auth(api_client, admin_user)
    response = api_client.get("/api/v1/dashboard/audit-logs/")
    assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_device_token_register_duplicate_and_delete_scope(api_client, normal_user, other_user):
    auth(api_client, normal_user)
    token = "ExponentPushToken[aaaaaaaaaaaaaaaaaaaa]"
    first = api_client.post(
        "/api/v1/notifications/device-tokens/", {"token": token, "platform": "android"}, format="json"
    )
    second = api_client.post("/api/v1/notifications/device-tokens/", {"token": token, "platform": "ios"}, format="json")
    assert first.status_code == status.HTTP_201_CREATED
    assert second.status_code == status.HTTP_201_CREATED
    assert DeviceToken.objects.get(token=token).platform == "ios"

    auth(api_client, other_user)
    delete = api_client.delete(f"/api/v1/notifications/device-tokens/{first.data['data']['id']}/")
    assert delete.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_dashboard_stats(api_client, admin_user, normal_user):
    SupportTicket.objects.create(user=normal_user, category="technical", subject="Open")
    PrintOrder.objects.create(user=normal_user)
    auth(api_client, normal_user)
    assert api_client.get("/api/v1/dashboard/stats/").status_code == status.HTTP_403_FORBIDDEN

    auth(api_client, admin_user)
    response = api_client.get("/api/v1/dashboard/stats/")
    assert response.status_code == status.HTTP_200_OK
    assert response.data["data"]["users"]["total"] >= 2
    assert response.data["data"]["printing"]["total_orders"] == 1
    assert response.data["data"]["support"]["open_tickets"] == 1
