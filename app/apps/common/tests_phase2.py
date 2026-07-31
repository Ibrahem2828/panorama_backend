import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.announcements.models import Announcement, AnnouncementTargetUserType
from apps.files.models import FileResource, FileVisibility
from apps.groups.models import Group, GroupMembership, GroupMembershipStatus
from apps.notifications.models import Notification
from apps.universities.models import AcademicYear, Faculty, Major, Semester, Subject, University
from apps.verification.models import VerificationRequest

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
        full_name="Admin User",
        email="admin@example.com",
        phone_number="+963911111111",
        password="StrongPass123!",
        role=UserRole.ADMIN,
    )


@pytest.fixture
def normal_user(db):
    return User.objects.create_user(
        full_name="Normal User",
        email="normal@example.com",
        phone_number="+963922222222",
        password="StrongPass123!",
        role=UserRole.NORMAL_USER,
    )


@pytest.fixture
def student_user(db):
    user = User.objects.create_user(
        full_name="Student User",
        email="student-phase2@example.com",
        phone_number="+963933333333",
        password="StrongPass123!",
        role=UserRole.STUDENT,
    )
    StudentProfile.objects.create(user=user)
    return user


@pytest.fixture
def academic_structure(db):
    university = University.objects.create(name="Damascus University", code="DU")
    faculty = Faculty.objects.create(university=university, name="Engineering", code="2")
    major = Major.objects.create(faculty=faculty, name="Software Engineering", code="SWE")
    other_faculty = Faculty.objects.create(university=university, name="Science", code="3")
    other_major = Major.objects.create(faculty=other_faculty, name="Math", code="MATH")
    year = AcademicYear.objects.create(name="First Year", order=1)
    semester = Semester.objects.create(name="First Semester", order=1)
    subject = Subject.objects.create(major=major, academic_year=year, semester=semester, name="Algorithms", code="ALG")
    return {
        "university": university,
        "faculty": faculty,
        "major": major,
        "other_major": other_major,
        "year": year,
        "semester": semester,
        "subject": subject,
    }


def authenticate(client, user):
    client.force_authenticate(user=user)


def uploaded_image(name="card.png"):
    return SimpleUploadedFile(name, PNG_BYTES, content_type="image/png")


def uploaded_file(name="lecture.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4 test", content_type="application/pdf")


def approve_student(user, academic):
    profile = user.student_profile
    profile.university = academic["university"]
    profile.faculty = academic["faculty"]
    profile.major = academic["major"]
    profile.academic_year = academic["year"]
    profile.semester = academic["semester"]
    profile.student_number = "2150094"
    profile.verification_status = StudentVerificationStatus.APPROVED
    profile.verified_at = timezone.now()
    profile.save()
    user.is_phone_verified = True
    user.save(update_fields=["is_phone_verified", "updated_at"])
    return profile


@pytest.mark.django_db
def test_admin_can_create_academic_structure(api_client, admin_user):
    authenticate(api_client, admin_user)
    response = api_client.post(
        "/api/v1/dashboard/universities/", {"name": "Aleppo University", "code": "AU"}, format="json"
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert University.objects.filter(code="AU").exists()


@pytest.mark.django_db
def test_normal_user_cannot_create_academic_structure(api_client, normal_user):
    authenticate(api_client, normal_user)
    response = api_client.post("/api/v1/dashboard/universities/", {"name": "Blocked", "code": "BLK"}, format="json")

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_public_academic_apis_return_only_active_records(api_client, academic_structure):
    University.objects.create(name="Inactive University", code="IU", is_active=False)

    response = api_client.get("/api/v1/universities/")

    assert response.status_code == status.HTTP_200_OK
    names = [item["name"] for item in response.data["data"]["results"]]
    assert "Damascus University" in names
    assert "Inactive University" not in names


@pytest.mark.django_db
def test_subject_filtering_works(api_client, academic_structure):
    response = api_client.get(
        f"/api/v1/majors/{academic_structure['major'].id}/subjects/?academic_year={academic_structure['year'].id}"
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data["data"]["count"] == 1


@pytest.mark.django_db
def test_student_can_update_profile_before_verification(api_client, student_user, academic_structure):
    authenticate(api_client, student_user)
    response = api_client.patch(
        "/api/v1/students/me/profile/",
        {
            "university": academic_structure["university"].id,
            "faculty": academic_structure["faculty"].id,
            "major": academic_structure["major"].id,
            "academic_year": academic_structure["year"].id,
            "semester": academic_structure["semester"].id,
            "student_number": "2150094",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    student_user.student_profile.refresh_from_db()
    assert student_user.student_profile.major == academic_structure["major"]


@pytest.mark.django_db
def test_approved_student_cannot_update_academic_profile(api_client, student_user, academic_structure):
    approve_student(student_user, academic_structure)
    authenticate(api_client, student_user)
    response = api_client.patch("/api/v1/students/me/profile/", {"student_number": "999"}, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_student_profile_invalid_hierarchy_rejected(api_client, student_user, academic_structure):
    authenticate(api_client, student_user)
    response = api_client.patch(
        "/api/v1/students/me/profile/",
        {
            "university": academic_structure["university"].id,
            "faculty": academic_structure["faculty"].id,
            "major": academic_structure["other_major"].id,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_verification_submit_duplicate_and_approval_flow(api_client, student_user, admin_user, academic_structure):
    student_user.is_phone_verified = True
    student_user.save(update_fields=["is_phone_verified", "updated_at"])
    authenticate(api_client, student_user)
    payload = {
        "university": academic_structure["university"].id,
        "faculty": academic_structure["faculty"].id,
        "major": academic_structure["major"].id,
        "academic_year": academic_structure["year"].id,
        "semester": academic_structure["semester"].id,
        "student_number": "2150094",
        "card_image": uploaded_image(),
    }
    response = api_client.post("/api/v1/verification/submit/", payload, format="multipart")
    duplicate = api_client.post(
        "/api/v1/verification/submit/", {**payload, "card_image": uploaded_image("card2.png")}, format="multipart"
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert duplicate.status_code == status.HTTP_400_BAD_REQUEST

    verification = VerificationRequest.objects.get(user=student_user)
    authenticate(api_client, admin_user)
    response = api_client.post(f"/api/v1/dashboard/verifications/{verification.id}/approve/", {}, format="json")

    assert response.status_code == status.HTTP_200_OK
    student_user.student_profile.refresh_from_db()
    assert student_user.student_profile.verification_status == StudentVerificationStatus.APPROVED
    assert Notification.objects.filter(user=student_user, type="verification").exists()


@pytest.mark.django_db
def test_verification_reject_creates_notification_and_non_admin_forbidden(
    api_client, student_user, normal_user, academic_structure
):
    approve_student(student_user, academic_structure)
    request = VerificationRequest.objects.create(
        user=student_user,
        student_profile=student_user.student_profile,
        university=academic_structure["university"],
        faculty=academic_structure["faculty"],
        major=academic_structure["major"],
        academic_year=academic_structure["year"],
        semester=academic_structure["semester"],
        student_number="2150094",
        card_image=uploaded_image(),
    )
    authenticate(api_client, normal_user)
    forbidden = api_client.post(
        f"/api/v1/dashboard/verifications/{request.id}/reject/", {"rejection_reason": "No"}, format="json"
    )
    assert forbidden.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_notifications_user_scope_and_read_actions(api_client, normal_user, admin_user):
    own = Notification.objects.create(user=normal_user, title="Own", body="Body")
    Notification.objects.create(user=admin_user, title="Other", body="Body")
    authenticate(api_client, normal_user)

    response = api_client.get("/api/v1/notifications/")
    assert response.data["data"]["count"] == 1

    count = api_client.get("/api/v1/notifications/unread-count/")
    assert count.data["data"]["count"] == 1

    read = api_client.post(f"/api/v1/notifications/{own.id}/read/")
    assert read.status_code == status.HTTP_200_OK

    Notification.objects.create(user=normal_user, title="Own2", body="Body")
    read_all = api_client.post("/api/v1/notifications/read-all/")
    assert read_all.status_code == status.HTTP_200_OK
    assert Notification.objects.filter(user=normal_user, is_read=False).count() == 0


@pytest.mark.django_db
def test_group_membership_flow_and_access_rules(api_client, admin_user, student_user, academic_structure):
    approve_student(student_user, academic_structure)
    authenticate(api_client, admin_user)
    create = api_client.post(
        "/api/v1/dashboard/groups/",
        {
            "name": "SWE Group",
            "university": academic_structure["university"].id,
            "major": academic_structure["major"].id,
        },
        format="json",
    )
    assert create.status_code == status.HTTP_201_CREATED
    group_id = create.data["data"]["id"]

    authenticate(api_client, student_user)
    available = api_client.get("/api/v1/groups/available/")
    assert available.data["data"]["count"] == 1

    join = api_client.post(f"/api/v1/groups/{group_id}/join/")
    assert join.status_code == status.HTTP_200_OK
    membership = GroupMembership.objects.get(group_id=group_id, user=student_user)

    authenticate(api_client, admin_user)
    approve = api_client.post(f"/api/v1/dashboard/group-memberships/{membership.id}/approve/")
    assert approve.status_code == status.HTTP_200_OK

    authenticate(api_client, student_user)
    mine = api_client.get("/api/v1/groups/my/")
    assert mine.data["data"]["count"] == 1
    leave = api_client.post(f"/api/v1/groups/{group_id}/leave/")
    assert leave.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_unverified_and_unrelated_students_cannot_join_group(api_client, admin_user, student_user, academic_structure):
    group = Group.objects.create(
        name="SWE Group",
        university=academic_structure["university"],
        major=academic_structure["major"],
        created_by=admin_user,
    )
    authenticate(api_client, student_user)
    unverified = api_client.post(f"/api/v1/groups/{group.id}/join/")
    assert unverified.status_code == status.HTTP_403_FORBIDDEN

    approve_student(student_user, academic_structure)
    student_user.student_profile.major = academic_structure["other_major"]
    student_user.student_profile.save()
    unrelated = api_client.post(f"/api/v1/groups/{group.id}/join/")
    assert unrelated.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_blocked_student_cannot_rejoin(api_client, admin_user, student_user, academic_structure):
    approve_student(student_user, academic_structure)
    group = Group.objects.create(name="SWE Group", university=academic_structure["university"], created_by=admin_user)
    GroupMembership.objects.create(group=group, user=student_user, status=GroupMembershipStatus.BLOCKED)
    authenticate(api_client, student_user)

    response = api_client.post(f"/api/v1/groups/{group.id}/join/")

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_file_access_control(api_client, admin_user, normal_user, student_user, academic_structure):
    approve_student(student_user, academic_structure)
    public_file = FileResource.objects.create(
        title="Public", file=uploaded_file(), uploaded_by=admin_user, visibility=FileVisibility.PUBLIC
    )
    verified_file = FileResource.objects.create(
        title="Verified",
        file=uploaded_file("v.pdf"),
        uploaded_by=admin_user,
        visibility=FileVisibility.VERIFIED_STUDENTS_ONLY,
    )
    major_file = FileResource.objects.create(
        title="Major",
        file=uploaded_file("m.pdf"),
        uploaded_by=admin_user,
        visibility=FileVisibility.MAJOR_ONLY,
        major=academic_structure["major"],
        academic_year=academic_structure["year"],
    )
    authenticate(api_client, normal_user)
    normal_response = api_client.get("/api/v1/files/")
    normal_titles = [item["title"] for item in normal_response.data["data"]["results"]]
    assert public_file.title in normal_titles
    assert verified_file.title not in normal_titles

    authenticate(api_client, student_user)
    student_response = api_client.get("/api/v1/files/")
    titles = [item["title"] for item in student_response.data["data"]["results"]]
    assert verified_file.title in titles
    assert major_file.title in titles


@pytest.mark.django_db
def test_group_only_file_requires_approved_membership(api_client, admin_user, student_user, academic_structure):
    approve_student(student_user, academic_structure)
    group = Group.objects.create(name="SWE Group", university=academic_structure["university"], created_by=admin_user)
    file_resource = FileResource.objects.create(
        title="Group File",
        file=uploaded_file(),
        uploaded_by=admin_user,
        visibility=FileVisibility.GROUP_ONLY,
        group=group,
    )
    authenticate(api_client, student_user)
    hidden = api_client.get("/api/v1/files/")
    assert file_resource.title not in [item["title"] for item in hidden.data["data"]["results"]]

    GroupMembership.objects.create(group=group, user=student_user, status=GroupMembershipStatus.APPROVED)
    visible = api_client.get("/api/v1/files/")
    assert file_resource.title in [item["title"] for item in visible.data["data"]["results"]]


@pytest.mark.django_db
def test_non_admin_cannot_upload_dashboard_file(api_client, normal_user):
    authenticate(api_client, normal_user)
    response = api_client.post(
        "/api/v1/dashboard/files/",
        {"title": "Blocked", "file": uploaded_file()},
        format="multipart",
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_announcement_targeting(api_client, admin_user, normal_user, student_user, academic_structure):
    approve_student(student_user, academic_structure)
    Announcement.objects.create(title="All", description="All", created_by=admin_user)
    Announcement.objects.create(
        title="Student",
        description="Student",
        target_user_type=AnnouncementTargetUserType.STUDENTS,
        created_by=admin_user,
    )
    Announcement.objects.create(
        title="Verified",
        description="Verified",
        target_user_type=AnnouncementTargetUserType.VERIFIED_STUDENTS,
        target_major=academic_structure["major"],
        created_by=admin_user,
    )
    Announcement.objects.create(title="Inactive", description="Inactive", created_by=admin_user, is_active=False)
    Announcement.objects.create(
        title="Expired",
        description="Expired",
        created_by=admin_user,
        ends_at=timezone.now() - timezone.timedelta(days=1),
    )

    authenticate(api_client, normal_user)
    normal = api_client.get("/api/v1/announcements/")
    normal_titles = [item["title"] for item in normal.data["data"]["results"]]
    assert "All" in normal_titles
    assert "Student" not in normal_titles

    authenticate(api_client, student_user)
    student = api_client.get("/api/v1/announcements/")
    titles = [item["title"] for item in student.data["data"]["results"]]
    assert "All" in titles
    assert "Student" in titles
    assert "Verified" in titles
    assert "Inactive" not in titles
    assert "Expired" not in titles
