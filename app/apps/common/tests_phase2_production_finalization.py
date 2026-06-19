import time

import pytest
from asgiref.sync import async_to_sync
from asgiref.testing import ApplicationCommunicator
from django.core import signing
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from config.asgi import application
from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.audit.models import AuditLog
from apps.chat.services import GROUP_CHAT_WS_TOKEN_SALT
from apps.files.models import FileResource, FileVisibility
from apps.groups.models import Group, GroupMembership, GroupMembershipRole, GroupMembershipStatus
from apps.printing.models import PrintOrder, PrintOrderItem
from apps.universities.models import AcademicYear, Faculty, Major, Semester, University
from apps.verification.models import VerificationRequest, VerificationStatus


GIF_BYTES = b"GIF87a\x01\x00\x01\x00\x80\x01\x00\x00\x00\x00ccc,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def users(db):
    admin = User.objects.create_user("final-admin@example.com", "+963988100001", "StrongPass123!", full_name="Final Admin", role=UserRole.ADMIN)
    it = User.objects.create_user("final-it@example.com", "+963988100002", "StrongPass123!", full_name="Final IT", role=UserRole.IT_SUPPORT)
    print_staff = User.objects.create_user("final-print@example.com", "+963988100003", "StrongPass123!", full_name="Final Print", role=UserRole.PRINT_STAFF)
    normal = User.objects.create_user("final-normal@example.com", "+963988100004", "StrongPass123!", full_name="Final Normal", role=UserRole.NORMAL_USER)
    student = User.objects.create_user("final-student@example.com", "+963988100005", "StrongPass123!", full_name="Final Student", role=UserRole.STUDENT, is_phone_verified=True)
    other_student = User.objects.create_user("final-other@example.com", "+963988100006", "StrongPass123!", full_name="Final Other", role=UserRole.STUDENT, is_phone_verified=True)
    StudentProfile.objects.create(user=student)
    StudentProfile.objects.create(user=other_student)
    return {
        "admin": admin,
        "it": it,
        "print_staff": print_staff,
        "normal": normal,
        "student": student,
        "other_student": other_student,
    }


@pytest.fixture
def academic(db):
    university = University.objects.create(name="Final University", code="FU")
    faculty = Faculty.objects.create(university=university, name="Dentistry", code="2")
    other_faculty = Faculty.objects.create(university=university, name="Pharmacy", code="3")
    major = Major.objects.create(faculty=faculty, name="Dental Surgery", code="DS")
    other_major = Major.objects.create(faculty=other_faculty, name="Pharmacy", code="PH")
    year = AcademicYear.objects.create(name="Year 2015", order=15)
    semester = Semester.objects.create(name="Semester 1", order=1)
    return {
        "university": university,
        "faculty": faculty,
        "other_faculty": other_faculty,
        "major": major,
        "other_major": other_major,
        "year": year,
        "semester": semester,
    }


def upload(name="file.pdf", content=b"%PDF-1.4 test", content_type="application/pdf"):
    return SimpleUploadedFile(name, content, content_type=content_type)


def image_upload(name="card.gif"):
    return SimpleUploadedFile(name, GIF_BYTES, content_type="image/gif")


def auth(client, user):
    client.force_authenticate(user=user)


def websocket_scope(path: str):
    raw_path, _, raw_query = path.partition("?")
    return {
        "type": "websocket",
        "path": raw_path,
        "raw_path": raw_path.encode("ascii"),
        "query_string": raw_query.encode("ascii"),
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "subprotocols": [],
    }


async def websocket_connect_send(path: str, payload: dict | None = None):
    communicator = ApplicationCommunicator(application, websocket_scope(path))
    await communicator.send_input({"type": "websocket.connect"})
    first = await communicator.receive_output(5)
    if first["type"] != "websocket.accept" or payload is None:
        await communicator.wait()
        return first, None
    await communicator.send_input({"type": "websocket.receive", "text": '{"type":"message","content":"hello"}'})
    second = await communicator.receive_output(5)
    await communicator.send_input({"type": "websocket.disconnect", "code": 1000})
    await communicator.wait()
    return first, second


def approve_student(user, academic, *, major=None):
    profile = user.student_profile
    profile.university = academic["university"]
    profile.faculty = academic["faculty"]
    profile.major = major or academic["major"]
    profile.academic_year = academic["year"]
    profile.semester = academic["semester"]
    profile.student_number = "2150094"
    profile.verification_status = StudentVerificationStatus.APPROVED
    profile.save()
    return profile


@pytest.mark.django_db
def test_protected_file_download_and_dashboard_preview_tokens(api_client, users, academic):
    approve_student(users["student"], academic)
    file_resource = FileResource.objects.create(
        title="Verified",
        file=upload(),
        uploaded_by=users["admin"],
        visibility=FileVisibility.VERIFIED_STUDENTS_ONLY,
    )

    auth(api_client, users["normal"])
    denied = api_client.post(f"/api/v1/files/{file_resource.id}/download-token/")
    assert denied.status_code == status.HTTP_403_FORBIDDEN
    assert denied.data["success"] is False
    assert "request_id" in denied.data

    auth(api_client, users["student"])
    allowed = api_client.post(f"/api/v1/files/{file_resource.id}/download-token/")
    assert allowed.status_code == status.HTTP_200_OK
    assert allowed.data["data"]["expires_in"] == 300
    assert "/api/v1/protected-media/" in allowed.data["data"]["url"]
    served = api_client.get(allowed.data["data"]["url"])
    assert served.status_code == status.HTTP_200_OK
    assert AuditLog.objects.filter(action="file_download_token_created", target_id=str(file_resource.id)).exists()

    auth(api_client, users["admin"])
    preview = api_client.post(f"/api/v1/dashboard/files/{file_resource.id}/preview-token/")
    assert preview.status_code == status.HTTP_200_OK

    auth(api_client, users["print_staff"])
    print_staff_denied = api_client.post(f"/api/v1/dashboard/files/{file_resource.id}/preview-token/")
    assert print_staff_denied.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_verification_card_and_print_file_preview_tokens(api_client, users, academic):
    profile = users["student"].student_profile
    verification = VerificationRequest.objects.create(
        user=users["student"],
        student_profile=profile,
        university=academic["university"],
        faculty=academic["faculty"],
        major=academic["major"],
        academic_year=academic["year"],
        semester=academic["semester"],
        student_number="2150094",
        card_image=image_upload(),
        status=VerificationStatus.PENDING,
    )

    auth(api_client, users["print_staff"])
    denied_card = api_client.post(f"/api/v1/dashboard/verifications/{verification.id}/card-preview-token/")
    assert denied_card.status_code == status.HTTP_403_FORBIDDEN

    auth(api_client, users["admin"])
    card = api_client.post(f"/api/v1/dashboard/verifications/{verification.id}/card-preview-token/")
    assert card.status_code == status.HTTP_200_OK
    assert api_client.get(card.data["data"]["url"]).status_code == status.HTTP_200_OK
    detail = api_client.get(f"/api/v1/dashboard/verifications/{verification.id}/")
    assert detail.data["data"]["card_image"] is None

    order = PrintOrder.objects.create(user=users["normal"])
    item = PrintOrderItem.objects.create(order=order, uploaded_file=upload("print.pdf"))
    auth(api_client, users["print_staff"])
    print_preview = api_client.post(f"/api/v1/dashboard/printing/orders/{order.id}/file-preview-token/", {"item_id": item.id}, format="json")
    assert print_preview.status_code == status.HTTP_200_OK
    assert api_client.get(print_preview.data["data"]["url"]).status_code == status.HTTP_200_OK
    assert AuditLog.objects.filter(action="print_file_preview_token_created", target_id=str(order.id)).exists()


@pytest.mark.django_db
def test_file_visibility_matrix_for_tokens(api_client, users, academic):
    approve_student(users["student"], academic)
    approve_student(users["other_student"], academic, major=academic["other_major"])
    group = Group.objects.create(name="Final Group", university=academic["university"], created_by=users["admin"])
    GroupMembership.objects.create(group=group, user=users["student"], status=GroupMembershipStatus.APPROVED)
    GroupMembership.objects.create(group=group, user=users["other_student"], status=GroupMembershipStatus.PENDING)

    major_file = FileResource.objects.create(
        title="Major",
        file=upload("major.pdf"),
        uploaded_by=users["admin"],
        visibility=FileVisibility.MAJOR_ONLY,
        major=academic["major"],
        academic_year=academic["year"],
    )
    group_file = FileResource.objects.create(
        title="Group",
        file=upload("group.pdf"),
        uploaded_by=users["admin"],
        visibility=FileVisibility.GROUP_ONLY,
        group=group,
    )
    admin_file = FileResource.objects.create(
        title="Admin",
        file=upload("admin.pdf"),
        uploaded_by=users["admin"],
        visibility=FileVisibility.ADMIN_ONLY,
    )

    auth(api_client, users["student"])
    assert api_client.post(f"/api/v1/files/{major_file.id}/download-token/").status_code == status.HTTP_200_OK
    assert api_client.post(f"/api/v1/files/{group_file.id}/download-token/").status_code == status.HTTP_200_OK
    list_response = api_client.get("/api/v1/files/")
    listed_ids = {item["id"] for item in list_response.data["data"]["results"]}
    assert admin_file.id not in listed_ids

    auth(api_client, users["other_student"])
    assert api_client.post(f"/api/v1/files/{major_file.id}/download-token/").status_code == status.HTTP_403_FORBIDDEN
    assert api_client.post(f"/api/v1/files/{group_file.id}/download-token/").status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_websocket_token_endpoint_permissions(api_client, users, academic):
    approve_student(users["student"], academic)
    approve_student(users["other_student"], academic)
    group = Group.objects.create(name="WS Group", university=academic["university"], created_by=users["admin"])
    GroupMembership.objects.create(group=group, user=users["student"], status=GroupMembershipStatus.APPROVED)
    GroupMembership.objects.create(group=group, user=users["other_student"], status=GroupMembershipStatus.BLOCKED)

    auth(api_client, users["normal"])
    assert api_client.post(f"/api/v1/groups/{group.id}/chat/ws-token/").status_code == status.HTTP_403_FORBIDDEN

    auth(api_client, users["other_student"])
    assert api_client.post(f"/api/v1/groups/{group.id}/chat/ws-token/").status_code == status.HTTP_403_FORBIDDEN

    auth(api_client, users["student"])
    token_response = api_client.post(f"/api/v1/groups/{group.id}/chat/ws-token/")
    assert token_response.status_code == status.HTTP_200_OK
    assert token_response.data["data"]["expires_in"] == 120
    assert "ws_token" in token_response.data["data"]
    assert "access_token" not in token_response.data["data"]["websocket_url"]


@pytest.mark.django_db(transaction=True)
def test_websocket_consumer_accepts_valid_token_and_rejects_wrong_group(api_client, users, academic):
    approve_student(users["student"], academic)
    group = Group.objects.create(name="WS Connect", university=academic["university"], created_by=users["admin"])
    other_group = Group.objects.create(name="WS Other", university=academic["university"], created_by=users["admin"])
    GroupMembership.objects.create(group=group, user=users["student"], status=GroupMembershipStatus.APPROVED)
    GroupMembership.objects.create(group=other_group, user=users["student"], status=GroupMembershipStatus.APPROVED)
    auth(api_client, users["student"])
    token = api_client.post(f"/api/v1/groups/{group.id}/chat/ws-token/").data["data"]["ws_token"]

    accepted, event = async_to_sync(websocket_connect_send)(f"/ws/v1/groups/{group.id}/chat/?token={token}", {"type": "message"})
    assert accepted["type"] == "websocket.accept"
    assert event["type"] == "websocket.send"
    assert '"type": "message"' in event["text"]

    rejected, _ = async_to_sync(websocket_connect_send)(f"/ws/v1/groups/{other_group.id}/chat/?token={token}")
    assert rejected["type"] == "websocket.close"


@pytest.mark.django_db(transaction=True)
def test_websocket_consumer_rejects_expired_token(users, academic, settings):
    approve_student(users["student"], academic)
    group = Group.objects.create(name="WS Expired", university=academic["university"], created_by=users["admin"])
    GroupMembership.objects.create(group=group, user=users["student"], status=GroupMembershipStatus.APPROVED)
    settings.GROUP_CHAT_WS_TOKEN_TTL_SECONDS = 1
    token = signing.dumps(
        {"user_id": users["student"].id, "group_id": group.id, "purpose": "group_chat_ws", "expires_in": 1},
        salt=GROUP_CHAT_WS_TOKEN_SALT,
    )
    time.sleep(1.2)
    rejected, _ = async_to_sync(websocket_connect_send)(f"/ws/v1/groups/{group.id}/chat/?token={token}")
    assert rejected["type"] == "websocket.close"


@pytest.mark.django_db
def test_response_contract_errors_and_pagination(api_client, users, monkeypatch):
    request_id = "phase2-final-request"
    validation = api_client.post(
        "/api/v1/auth/login/",
        {"identifier": users["normal"].email, "password": "bad"},
        format="json",
        HTTP_X_REQUEST_ID=request_id,
    )
    assert validation.status_code == status.HTTP_400_BAD_REQUEST
    assert validation.data["success"] is False
    assert validation.data["request_id"] == request_id

    auth(api_client, users["normal"])
    permission = api_client.get("/api/v1/dashboard/stats/", HTTP_X_REQUEST_ID=request_id)
    assert permission.status_code == status.HTTP_403_FORBIDDEN
    assert permission.data["request_id"] == request_id

    api_client.force_authenticate(user=None)
    cache.clear()
    monkeypatch.setitem(ScopedRateThrottle.THROTTLE_RATES, "login", "1/minute")
    first = api_client.post("/api/v1/auth/login/", {"identifier": users["normal"].email, "password": "bad"}, format="json", HTTP_X_REQUEST_ID=request_id)
    second = api_client.post("/api/v1/auth/login/", {"identifier": users["normal"].email, "password": "bad"}, format="json", HTTP_X_REQUEST_ID=request_id)
    assert first.status_code == status.HTTP_400_BAD_REQUEST
    assert second.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert second.data["request_id"] == request_id

    auth(api_client, users["admin"])
    FileResource.objects.create(title="Page", file=upload("page.pdf"), uploaded_by=users["admin"], visibility=FileVisibility.PUBLIC)
    paginated = api_client.get("/api/v1/dashboard/files/?page_size=1")
    assert paginated.status_code == status.HTTP_200_OK
    assert paginated.data["success"] is True
    assert paginated.data["data"]["count"] >= 1
    assert isinstance(paginated.data["data"]["results"], list)
