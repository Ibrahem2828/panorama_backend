import json
from pathlib import Path

import pytest
from django.core.management import call_command
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.chat.services import ChatPermissionService
from apps.groups.models import Group, GroupMembership, GroupMembershipRole, GroupMembershipStatus
from apps.universities.models import AcademicYear, Faculty, Major, Semester, University


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(
        full_name="Admin",
        email="hard-admin@example.com",
        phone_number="+963966000001",
        password="StrongPass123!",
        role=UserRole.ADMIN,
    )


@pytest.fixture
def print_staff(db):
    return User.objects.create_user(
        full_name="Print Staff",
        email="hard-print@example.com",
        phone_number="+963966000002",
        password="StrongPass123!",
        role=UserRole.PRINT_STAFF,
    )


@pytest.fixture
def normal_user(db):
    return User.objects.create_user(
        full_name="Normal",
        email="hard-normal@example.com",
        phone_number="+963966000003",
        password="StrongPass123!",
        role=UserRole.NORMAL_USER,
    )


@pytest.fixture
def student_user(db):
    user = User.objects.create_user(
        full_name="Student",
        email="hard-student@example.com",
        phone_number="+963966000004",
        password="StrongPass123!",
        role=UserRole.STUDENT,
    )
    StudentProfile.objects.create(user=user)
    return user


@pytest.fixture
def academic(db):
    university = University.objects.create(name="Hardening University", code="HU")
    faculty = Faculty.objects.create(university=university, name="Dentistry", code="2")
    major = Major.objects.create(faculty=faculty, name="Dentistry", code="2")
    year = AcademicYear.objects.create(name="First", order=1)
    semester = Semester.objects.create(name="One", order=1)
    return {"university": university, "faculty": faculty, "major": major, "year": year, "semester": semester}


def authenticate(client, user):
    client.force_authenticate(user=user)


def approve_student(user, academic):
    profile = user.student_profile
    profile.university = academic["university"]
    profile.faculty = academic["faculty"]
    profile.major = academic["major"]
    profile.academic_year = academic["year"]
    profile.semester = academic["semester"]
    profile.verification_status = StudentVerificationStatus.APPROVED
    profile.student_number = "2150094"
    profile.save()
    user.is_phone_verified = True
    user.save(update_fields=["is_phone_verified", "updated_at"])


@pytest.mark.django_db
def test_group_image_and_metadata_are_serialized(api_client, admin_user, student_user, academic):
    approve_student(student_user, academic)
    group = Group.objects.create(
        name="Image Group",
        university=academic["university"],
        created_by=admin_user,
        send_messages_permission="all_members",
    )
    GroupMembership.objects.create(group=group, user=student_user, status=GroupMembershipStatus.APPROVED)
    authenticate(api_client, student_user)

    response = api_client.get(f"/api/v1/groups/{group.id}/")

    assert response.status_code == status.HTTP_200_OK
    data = response.data["data"]
    assert "image" in data
    assert data["send_messages_permission"] == "all_members"
    assert data["current_user_membership_status"] == GroupMembershipStatus.APPROVED
    assert data["current_user_group_role"] == GroupMembershipRole.MEMBER
    assert data["members_count"] == 1


@pytest.mark.django_db
def test_group_send_permissions_rest_and_service(api_client, admin_user, student_user, academic):
    approve_student(student_user, academic)
    all_members = Group.objects.create(
        name="Open Send",
        university=academic["university"],
        created_by=admin_user,
        send_messages_permission="all_members",
    )
    admins_only = Group.objects.create(
        name="Admin Send",
        university=academic["university"],
        created_by=admin_user,
        send_messages_permission="admins_only",
    )
    GroupMembership.objects.create(group=all_members, user=student_user, status=GroupMembershipStatus.APPROVED)
    membership = GroupMembership.objects.create(
        group=admins_only, user=student_user, status=GroupMembershipStatus.APPROVED
    )
    authenticate(api_client, student_user)

    assert (
        api_client.post(f"/api/v1/groups/{all_members.id}/messages/", {"content": "hello"}, format="json").status_code
        == status.HTTP_201_CREATED
    )
    assert (
        api_client.post(f"/api/v1/groups/{admins_only.id}/messages/", {"content": "blocked"}, format="json").status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert ChatPermissionService.can_send_message(student_user, admins_only) is False

    membership.role = GroupMembershipRole.MODERATOR
    membership.save(update_fields=["role", "updated_at"])
    assert ChatPermissionService.can_send_message(student_user, admins_only) is True
    assert (
        api_client.post(f"/api/v1/groups/{admins_only.id}/messages/", {"content": "allowed"}, format="json").status_code
        == status.HTTP_201_CREATED
    )

    authenticate(api_client, admin_user)
    assert (
        api_client.post(f"/api/v1/groups/{admins_only.id}/messages/", {"content": "admin"}, format="json").status_code
        == status.HTTP_201_CREATED
    )


@pytest.mark.django_db
def test_membership_role_update_and_print_staff_dashboard_scope(
    api_client, admin_user, print_staff, student_user, academic
):
    approve_student(student_user, academic)
    group = Group.objects.create(name="Role Group", university=academic["university"], created_by=admin_user)
    membership = GroupMembership.objects.create(group=group, user=student_user, status=GroupMembershipStatus.APPROVED)
    authenticate(api_client, admin_user)

    response = api_client.patch(
        f"/api/v1/dashboard/group-memberships/{membership.id}/role/", {"role": "group_admin"}, format="json"
    )
    assert response.status_code == status.HTTP_200_OK
    membership.refresh_from_db()
    assert membership.role == GroupMembershipRole.GROUP_ADMIN

    authenticate(api_client, print_staff)
    assert api_client.get("/api/v1/dashboard/printing/orders/").status_code == status.HTTP_200_OK
    assert api_client.get("/api/v1/dashboard/groups/").status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_seed_initial_data_idempotent_and_creates_demo_data():
    call_command("seed_initial_data")
    call_command("seed_initial_data")

    assert Faculty.objects.filter(code__in=[str(i) for i in range(1, 8)]).count() == 7
    assert User.objects.filter(email="it@panorama.local", role=UserRole.IT_SUPPORT).exists()
    assert User.objects.filter(email="admin@panorama.local", role=UserRole.ADMIN).exists()
    assert User.objects.filter(email="print@panorama.local", role=UserRole.PRINT_STAFF).exists()
    student = User.objects.get(email="student@panorama.local")
    assert student.student_profile.verification_status == StudentVerificationStatus.APPROVED
    assert student.student_profile.faculty_code_from_student_number == "2"
    assert Group.objects.filter(name="طب الأسنان - السنة الأولى").count() == 1


def test_api_collection_json_files_are_valid_and_scoped():
    root = Path(__file__).resolve().parents[3]
    mobile_path = root / "integrations" / "api" / "panorama-mobile-api.postman_collection.json"
    dashboard_path = root / "integrations" / "api" / "panorama-dashboard-api.postman_collection.json"

    mobile = json.loads(mobile_path.read_text(encoding="utf-8"))
    dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))

    assert mobile["info"]["schema"] == "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
    assert dashboard["info"]["schema"] == "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
    assert {variable["key"] for variable in mobile["variable"]} >= {"base_url", "access_token", "installation_id"}
    assert {variable["key"] for variable in dashboard["variable"]} >= {"base_url", "access_token", "request_id"}
    assert "/dashboard/" not in mobile_path.read_text(encoding="utf-8")
    assert "/api/v1/dashboard/" in dashboard_path.read_text(encoding="utf-8")


def test_canonical_documentation_exists():
    root = Path(__file__).resolve().parents[3]
    docs = root / "docs"
    assert (docs / "INDEX.md").exists()
    assert (docs / "API_SECURITY_AND_AUTH.md").exists()
    assert (docs / "QUALITY_TESTING_AND_RELEASE.md").exists()
    assert (docs / "MOBILE_PRODUCT_INTEGRATION.md").exists()
    assert (docs / "DASHBOARD_INTEGRATION.md").exists()
