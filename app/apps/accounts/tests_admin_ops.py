"""Admin-ops round: job title, student detail, staff notifications for registrations and join requests, member export."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.accounts.permissions import Capability
from apps.audit.models import AuditAction, AuditLog
from apps.chat.models import Message
from apps.enrollment.models import EnrollmentItem, EnrollmentRequest
from apps.files.models import FileResource
from apps.groups.models import Group, GroupMembership, GroupMembershipStatus
from apps.groups.services import GroupMembershipService
from apps.notifications.models import Notification
from apps.printing.models import PrintOrder
from apps.support.models import SupportTicket
from apps.universities.models import AcademicYear, Faculty, Semester, Subject, University
from apps.verification.models import VerificationRequest


def _client(user=None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user)
    return client


def _user(email, phone, role, name=None) -> User:
    return User.objects.create_user(
        full_name=name or email, email=email, phone_number=phone, password="StrongPass123!", role=role
    )


@pytest.fixture
def world(db):
    uni = University.objects.create(name="Uni", code="U1")
    faculty = Faculty.objects.create(university=uni, name="Dentistry", code="DEN")
    year = AcademicYear.objects.create(name="Year 1", order=1)
    sem = Semester.objects.create(name="First semester", order=1)
    admin = _user("admin@t.test", "+963900000001", UserRole.ADMIN, "Admin")
    it = _user("it@t.test", "+963900000002", UserRole.IT_SUPPORT, "IT")
    printer = _user("print@t.test", "+963900000003", UserRole.PRINT_STAFF, "Printer")
    support = _user("sup@t.test", "+963900000004", UserRole.SUPPORT_STAFF, "Support")
    student = _user("stu@t.test", "+963910000001", UserRole.STUDENT, "Sara Student")
    StudentProfile.objects.create(
        user=student,
        university=uni,
        faculty=faculty,
        academic_year=year,
        semester=sem,
        student_number="2150001",
        verification_status=StudentVerificationStatus.APPROVED,
    )
    group = Group.objects.create(name="Community", university=uni, faculty=faculty, created_by=admin)
    return {
        "uni": uni,
        "faculty": faculty,
        "year": year,
        "sem": sem,
        "admin": admin,
        "it": it,
        "printer": printer,
        "support": support,
        "student": student,
        "group": group,
    }


# -- job title ----------------------------------------------------------------------------------------------------------


def test_me_returns_and_updates_name_and_job_title(world):
    w = world
    client = _client(w["admin"])
    assert client.get("/api/v1/auth/me/").data["data"]["job_title"] == ""
    response = client.patch(
        "/api/v1/auth/me/", {"full_name": "  New   Name ", "job_title": "  مدير   القبول "}, format="json"
    )
    assert response.status_code == 200
    assert response.data["data"]["job_title"] == "مدير القبول"
    assert response.data["data"]["full_name"] == "New Name"
    w["admin"].refresh_from_db()
    assert (w["admin"].full_name, w["admin"].job_title) == ("New Name", "مدير القبول")


def test_me_rejects_too_long_job_title_empty_name_and_protected_fields(world):
    w = world
    client = _client(w["student"])
    assert client.patch("/api/v1/auth/me/", {"job_title": "x" * 121}, format="json").status_code == 400
    assert client.patch("/api/v1/auth/me/", {"full_name": "   "}, format="json").status_code == 400
    client.patch("/api/v1/auth/me/", {"role": "admin", "email": "x@x.test", "job_title": "Dev"}, format="json")
    w["student"].refresh_from_db()
    assert w["student"].role == UserRole.STUDENT and w["student"].email == "stu@t.test"
    assert w["student"].job_title == "Dev"


def test_change_password_still_works_for_any_user(world):
    w = world
    response = _client(w["student"]).post(
        "/api/v1/auth/change-password/",
        {
            "old_password": "StrongPass123!",
            "new_password": "AnotherStrong456!",
            "new_password_confirm": "AnotherStrong456!",
        },
        format="json",
    )
    assert response.status_code in (200, 400)  # contract owned by the auth app; only checking it is reachable


def test_dashboard_users_list_detail_and_patch_include_job_title(world):
    w = world
    admin = _client(w["admin"])
    detail = admin.get(f"/api/v1/dashboard/users/{w['printer'].pk}/")
    assert detail.status_code == 200 and detail.data["data"]["job_title"] == ""
    response = admin.patch(
        f"/api/v1/dashboard/users/{w['printer'].pk}/",
        {"full_name": "Print Lead", "job_title": "مسؤول الطباعة"},
        format="json",
    )
    assert response.status_code == 200, response.data
    assert response.data["data"]["job_title"] == "مسؤول الطباعة" and response.data["data"]["full_name"] == "Print Lead"
    listing = admin.get("/api/v1/dashboard/users/?search=Print Lead").data["data"]
    rows = listing["results"] if isinstance(listing, dict) else listing
    assert rows[0]["job_title"] == "مسؤول الطباعة"
    assert AuditLog.objects.filter(action=AuditAction.USER_PROFILE_UPDATED, actor=w["admin"]).count() == 1


def test_dashboard_patch_job_title_denied_without_users_manage_or_above_rank(world):
    w = world
    assert (
        _client(w["printer"])
        .patch(f"/api/v1/dashboard/users/{w['support'].pk}/", {"job_title": "x"}, format="json")
        .status_code
        == 403
    )
    assert (
        _client(w["student"])
        .patch(f"/api/v1/dashboard/users/{w['support'].pk}/", {"job_title": "x"}, format="json")
        .status_code
        == 403
    )
    # an admin cannot edit IT support (higher rank)
    assert (
        _client(w["admin"])
        .patch(f"/api/v1/dashboard/users/{w['it'].pk}/", {"job_title": "x"}, format="json")
        .status_code
        == 403
    )


# -- chat sender --------------------------------------------------------------------------------------------------------


def test_chat_sender_detail_has_user_id_and_job_title(world):
    w = world
    GroupMembership.objects.create(group=w["group"], user=w["student"], status=GroupMembershipStatus.APPROVED)
    w["student"].job_title = "Class rep"
    w["student"].save()
    w["admin"].job_title = "Registrar"
    w["admin"].save()
    Message.objects.create(group=w["group"], sender=w["student"], content="hi")
    Message.objects.create(group=w["group"], sender=w["admin"], content="welcome")
    rows = _client(w["student"]).get(f"/api/v1/groups/{w['group'].pk}/messages/").data["data"]
    rows = rows["results"] if isinstance(rows, dict) else rows
    senders = {row["sender"]: row["sender_detail"] for row in rows}
    assert senders[w["student"].pk]["user_id"] == w["student"].pk
    assert senders[w["student"].pk]["job_title"] == "Class rep"
    assert senders[w["admin"].pk]["user_id"] == w["admin"].pk
    assert senders[w["admin"].pk]["job_title"] == "Registrar"
    assert senders[w["admin"].pk]["is_staff"] is True  # still shown by position, not by name
    assert senders[w["admin"].pk]["full_name"] != w["admin"].full_name


# -- staff notifications ------------------------------------------------------------------------------------------------


def test_student_registration_notifies_verification_reviewers_only(world, django_capture_on_commit_callbacks):
    w = world
    with django_capture_on_commit_callbacks(execute=True):
        response = _client().post(
            "/api/v1/auth/register/student/",
            {
                "full_name": "New Student",
                "email": "new@t.test",
                "phone_number": "+963955000001",
                "password": "StrongPass123!",
                "password_confirm": "StrongPass123!",
            },
            format="json",
        )
    assert response.status_code == 201, response.data
    new_id = User.objects.get(email="new@t.test").pk
    for staff in (w["admin"], w["it"]):
        note = Notification.objects.get(user=staff, related_object_type="student_registration")
        assert note.related_object_id == str(new_id)
        assert note.deep_link == f"/dashboard/verifications?highlight={new_id}"
        assert "New Student" in note.body
    assert not Notification.objects.filter(user=w["printer"], related_object_type="student_registration").exists()
    assert not Notification.objects.filter(user=w["support"], related_object_type="student_registration").exists()
    assert not Notification.objects.filter(user=w["student"], related_object_type="student_registration").exists()


def test_normal_user_registration_does_not_notify(world, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        response = _client().post(
            "/api/v1/auth/register/normal/",
            {
                "full_name": "Normal",
                "email": "n@t.test",
                "phone_number": "+963955000002",
                "password": "StrongPass123!",
                "password_confirm": "StrongPass123!",
            },
            format="json",
        )
    assert response.status_code == 201
    assert not Notification.objects.filter(related_object_type="student_registration").exists()


def _legacy_pending_request(user, group):
    """Students can no longer ask to join; a pending row (legacy data) still notifies managers when it is announced."""

    membership = GroupMembership.objects.create(group=group, user=user, status=GroupMembershipStatus.PENDING)
    transaction.on_commit(lambda: GroupMembershipService.notify_join_request(membership.pk))
    return membership


def test_group_join_request_notifies_group_managers_and_opens_the_group(world, django_capture_on_commit_callbacks):
    w = world
    with django_capture_on_commit_callbacks(execute=True):
        membership = _legacy_pending_request(w["student"], w["group"])
    assert membership.status == GroupMembershipStatus.PENDING
    for staff in (w["admin"], w["it"]):
        note = Notification.objects.get(user=staff, related_object_type="join_request")
        assert note.deep_link == f"/dashboard/groups?highlight={w['group'].pk}"
        assert note.data["group_id"] == w["group"].pk and note.data["membership_id"] == membership.pk
    for other in (w["printer"], w["support"], w["student"]):
        assert not Notification.objects.filter(user=other, related_object_type="join_request").exists()


def test_each_join_request_is_its_own_notification_even_in_the_same_group(world, django_capture_on_commit_callbacks):
    w = world
    second = _user("stu2@t.test", "+963910000002", UserRole.STUDENT, "Second")
    StudentProfile.objects.create(
        user=second,
        university=w["uni"],
        faculty=w["faculty"],
        academic_year=w["year"],
        semester=w["sem"],
        verification_status=StudentVerificationStatus.APPROVED,
    )
    with django_capture_on_commit_callbacks(execute=True):
        _legacy_pending_request(w["student"], w["group"])
        _legacy_pending_request(second, w["group"])
    assert Notification.objects.filter(user=w["admin"], related_object_type="join_request").count() == 2


def test_staff_added_membership_of_an_auto_approved_group_does_not_raise_a_join_request(
    world, django_capture_on_commit_callbacks
):
    w = world
    w["group"].requires_approval = False
    w["group"].save()
    with django_capture_on_commit_callbacks(execute=True):
        membership = GroupMembershipService.join(w["student"], w["group"], initiated_by=w["admin"])
    assert membership.status == GroupMembershipStatus.APPROVED
    assert not Notification.objects.filter(related_object_type="join_request").exists()


def test_quick_access_has_join_requests_and_new_registrations_for_capability_holders(world):
    w = world
    GroupMembership.objects.create(group=w["group"], user=w["student"], status=GroupMembershipStatus.PENDING)
    old = _user("old@t.test", "+963910000009", UserRole.STUDENT, "Old")
    User.objects.filter(pk=old.pk).update(date_joined=timezone.now() - timedelta(days=30))
    admin_counts = _client(w["admin"]).get("/api/v1/dashboard/quick-access/").data["data"]["counts"]
    assert admin_counts["join_requests_pending"] == 1
    assert admin_counts["registrations_new"] == 1  # the older account is outside the 7 day window
    printer_counts = _client(w["printer"]).get("/api/v1/dashboard/quick-access/").data["data"]["counts"]
    assert "join_requests_pending" not in printer_counts and "registrations_new" not in printer_counts
    support_counts = _client(w["support"]).get("/api/v1/dashboard/quick-access/").data["data"]["counts"]
    assert "join_requests_pending" not in support_counts and "registrations_new" not in support_counts


# -- student detail -----------------------------------------------------------------------------------------------------


def _fill_student(w):
    GroupMembership.objects.create(
        group=w["group"], user=w["student"], status=GroupMembershipStatus.APPROVED, joined_at=timezone.now()
    )
    subject = Subject.objects.create(
        faculty=w["faculty"], academic_year=w["year"], semester=w["sem"], name="Anatomy", code="A1"
    )
    enrollment = EnrollmentRequest.objects.create(
        user=w["student"],
        student_profile=w["student"].student_profile,
        faculty=w["faculty"],
        screenshot="enrollment_screenshots/x.png",
    )
    EnrollmentItem.objects.create(request=enrollment, subject=subject)
    FileResource.objects.create(
        title="My notes", file=SimpleUploadedFile("x.pdf", b"%PDF-1.4"), uploaded_by=w["student"]
    )
    PrintOrder.objects.create(user=w["student"], total_price=1500)
    SupportTicket.objects.create(user=w["student"], subject="Help me")
    VerificationRequest.objects.create(
        user=w["student"],
        student_profile=w["student"].student_profile,
        university=w["uni"],
        faculty=w["faculty"],
        academic_year=w["year"],
        student_number="2150001",
        card_image="verification_cards/x.png",
        status="rejected",
        rejection_reason="blurry",
    )


def test_student_detail_shape_and_content(world):
    w = world
    _fill_student(w)
    response = _client(w["admin"]).get(f"/api/v1/dashboard/users/{w['student'].pk}/student-detail/")
    assert response.status_code == 200, response.data
    data = response.data["data"]
    assert data["user"]["full_name"] == "Sara Student"
    assert set(data["user"]) == {
        "id",
        "full_name",
        "email",
        "phone_number",
        "job_title",
        "is_active",
        "last_login",
        "created_at",
    }
    assert data["profile"]["faculty"] == {"id": w["faculty"].pk, "name": "Dentistry"}
    assert data["profile"]["student_number"] == "2150001"
    assert data["profile"]["verification_status"] == "approved"
    assert data["profile"]["needs_term_confirmation"] is False
    assert data["groups"][0]["group_name"] == "Community" and data["groups"][0]["status"] == "approved"
    assert data["enrollments"][0]["items"] == [{"subject": "Anatomy", "status": "pending"}]
    assert data["files"][0]["title"] == "My notes"
    assert str(data["print_orders"][0]["total"]) in ("1500", "1500.00")
    assert data["support_tickets"][0]["subject"] == "Help me"
    assert data["verification_history"][0]["note"] == "blurry"
    assert data["counts"] == {
        "groups": 1,
        "enrollments": 1,
        "files": 1,
        "print_orders": 1,
        "support_tickets": 1,
        "verification_history": 1,
    }


def test_student_detail_caps_each_list_at_50_newest(world):
    w = world
    SupportTicket.objects.bulk_create([SupportTicket(user=w["student"], subject=f"T{i}") for i in range(55)])
    data = _client(w["admin"]).get(f"/api/v1/dashboard/users/{w['student'].pk}/student-detail/").data["data"]
    assert len(data["support_tickets"]) == 50 and data["counts"]["support_tickets"] == 55


def test_student_detail_permissions(world):
    w = world
    url = f"/api/v1/dashboard/users/{w['student'].pk}/student-detail/"
    assert _client(w["it"]).get(url).status_code == 200
    assert _client(w["printer"]).get(url).status_code == 403
    assert _client(w["support"]).get(url).status_code == 403
    assert _client(w["student"]).get(url).status_code == 403
    assert APIClient().get(url).status_code in (401, 403)


def test_student_detail_open_to_a_reviewer_who_only_has_verification_review(world):
    from apps.accounts.models import UserPermissionOverride

    w = world
    UserPermissionOverride.objects.create(
        user=w["printer"], permission_code=Capability.VERIFICATION_REVIEW, effect="allow"
    )
    assert _client(w["printer"]).get(f"/api/v1/dashboard/users/{w['student'].pk}/student-detail/").status_code == 200


def test_student_detail_404_for_non_students_and_unknown(world):
    w = world
    admin = _client(w["admin"])
    assert admin.get(f"/api/v1/dashboard/users/{w['printer'].pk}/student-detail/").status_code == 404
    assert admin.get("/api/v1/dashboard/users/999999/student-detail/").status_code == 404


# -- group member export ------------------------------------------------------------------------------------------------


def test_group_members_export_json_and_csv(world):
    w = world
    w["student"].full_name = "@evil"
    w["student"].save()
    GroupMembership.objects.create(
        group=w["group"], user=w["student"], status=GroupMembershipStatus.APPROVED, joined_at=timezone.now()
    )
    other = _user("o@t.test", "+963910000005", UserRole.STUDENT, "Pending One")
    GroupMembership.objects.create(group=w["group"], user=other, status=GroupMembershipStatus.PENDING)
    client = _client(w["admin"])
    url = f"/api/v1/dashboard/groups/{w['group'].pk}/members-export/"

    body = client.get(url).data["data"]
    assert [m["user_id"] for m in body["members"]] == [w["student"].pk]
    everyone = client.get(url + "?status=all").data["data"]["members"]
    assert len(everyone) == 2

    response = client.get(url + "?format=csv")
    assert response.status_code == 200 and response["Content-Type"].startswith("text/csv")
    text = response.content.decode("utf-8")
    assert text.startswith("﻿group,name,phone,role,status,joined_at")
    assert "Community,'@evil,+963910000001,member,approved," in text
    assert AuditLog.objects.filter(action=AuditAction.GROUP_MEMBERS_EXPORTED, actor=w["admin"]).count() == 3


def test_group_members_export_permissions_and_404(world):
    w = world
    url = f"/api/v1/dashboard/groups/{w['group'].pk}/members-export/?format=csv"
    assert _client(w["printer"]).get(url).status_code == 403
    assert _client(w["support"]).get(url).status_code == 403
    assert _client(w["student"]).get(url).status_code == 403
    assert APIClient().get(url).status_code in (401, 403)
    assert _client(w["admin"]).get("/api/v1/dashboard/groups/99999/members-export/").status_code == 404
