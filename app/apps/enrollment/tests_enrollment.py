"""Subject enrollment: submit, search, review (all / selected / reject), bulk, community auto-join, no self-join."""

from __future__ import annotations

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from PIL import Image
from rest_framework.test import APIClient

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.enrollment.models import EnrollmentRequest, EnrollmentStatus
from apps.groups.models import Group, GroupMembership, GroupMembershipStatus
from apps.notifications.models import Notification
from apps.universities.models import AcademicYear, Faculty, Semester, Subject, University


def _png(name="shot.png") -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(buffer, "PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def _client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


def _rows(response):
    data = response.data["data"]
    return data["results"] if isinstance(data, dict) and "results" in data else data


@pytest.fixture
def world(db):
    university = University.objects.create(name="Test University", code="TU")
    faculty = Faculty.objects.create(university=university, name="Dentistry", code="DEN")
    other_faculty = Faculty.objects.create(university=university, name="Law", code="LAW")
    year = AcademicYear.objects.create(name="Year 1", order=1)
    semester = Semester.objects.create(name="Semester 1", order=1)
    admin = User.objects.create_user(
        full_name="Admin",
        email="admin@t.test",
        phone_number="+963991000001",
        password="StrongPass123!",
        role=UserRole.ADMIN,
    )
    student = User.objects.create_user(
        full_name="Sara Student",
        email="sara@t.test",
        phone_number="+963991000002",
        password="StrongPass123!",
        role=UserRole.STUDENT,
    )
    StudentProfile.objects.create(
        user=student,
        university=university,
        faculty=faculty,
        academic_year=year,
        semester=semester,
        student_number="2150001",
        verification_status=StudentVerificationStatus.APPROVED,
    )
    subjects = {
        name: Subject.objects.create(faculty=faculty, academic_year=year, semester=semester, name=name, code=code)
        for name, code in (("Dental Anatomy", "DA101"), ("Dental Materials", "DM102"), ("Oral Biology", "OB103"))
    }
    Subject.objects.create(faculty=other_faculty, academic_year=year, semester=semester, name="Dental Law", code="LW1")
    groups = {
        name: Group.objects.create(
            name=f"{name} group", university=university, faculty=faculty, subject=subject, created_by=admin
        )
        for name, subject in subjects.items()
    }
    community = Group.objects.create(
        name="Dentistry community", university=university, faculty=faculty, created_by=admin
    )
    return {
        "admin": admin,
        "student": student,
        "subjects": subjects,
        "groups": groups,
        "community": community,
        "faculty": faculty,
    }


def _submit(world, names=("Dental Anatomy", "Dental Materials")):
    ids = [world["subjects"][name].pk for name in names]
    return _client(world["student"]).post(
        "/api/v1/enrollment/requests/", {"screenshot": _png(), "subject_ids": ids}, format="multipart"
    )


def _is_member(user, group) -> bool:
    return GroupMembership.objects.filter(user=user, group=group, status=GroupMembershipStatus.APPROVED).exists()


def test_search_needs_two_characters_and_never_lists_everything(world):
    client = _client(world["student"])
    assert _rows(client.get("/api/v1/enrollment/subjects/")) == []
    assert _rows(client.get("/api/v1/enrollment/subjects/?q=d")) == []
    names = [row["name"] for row in _rows(client.get("/api/v1/enrollment/subjects/?q=dental"))]
    assert names == ["Dental Anatomy", "Dental Materials"]  # other faculty's "Dental Law" is not offered
    assert [row["code"] for row in _rows(client.get("/api/v1/enrollment/subjects/?q=OB1"))] == ["OB103"]


def test_submit_creates_a_pending_request_and_blocks_a_second_one(world):
    response = _submit(world)
    assert response.status_code == 201, response.data
    data = response.data["data"]
    assert data["status"] == "pending"
    assert {item["subject"]["name"] for item in data["items"]} == {"Dental Anatomy", "Dental Materials"}
    assert _submit(world).status_code == 409


def test_pending_subjects_drop_out_of_search(world):
    _submit(world, ("Dental Anatomy",))
    names = [row["name"] for row in _rows(_client(world["student"]).get("/api/v1/enrollment/subjects/?q=dental"))]
    assert names == ["Dental Materials"]


def test_submit_validates_subjects_and_image(world):
    client = _client(world["student"])
    law = Subject.objects.get(name="Dental Law")
    bad_subject = client.post(
        "/api/v1/enrollment/requests/", {"screenshot": _png(), "subject_ids": [law.pk]}, format="multipart"
    )
    assert bad_subject.status_code == 400
    not_image = client.post(
        "/api/v1/enrollment/requests/",
        {"screenshot": SimpleUploadedFile("x.png", b"not an image", content_type="image/png"), "subject_ids": [1]},
        format="multipart",
    )
    assert not_image.status_code == 400
    assert client.post("/api/v1/enrollment/requests/", {"screenshot": _png()}, format="multipart").status_code == 400


def test_unverified_student_cannot_submit(world):
    profile = world["student"].student_profile
    profile.verification_status = StudentVerificationStatus.PENDING
    profile.save()
    assert _submit(world).status_code == 400


def test_cancel_then_resubmit(world):
    enrollment_id = _submit(world).data["data"]["id"]
    cancelled = _client(world["student"]).post(f"/api/v1/enrollment/requests/{enrollment_id}/cancel/")
    assert cancelled.status_code == 200 and cancelled.data["data"]["status"] == "cancelled"
    assert _client(world["student"]).post(f"/api/v1/enrollment/requests/{enrollment_id}/cancel/").status_code == 409
    assert _submit(world).status_code == 201


def test_approve_all_adds_the_student_to_every_subject_group_and_notifies(world):
    enrollment_id = _submit(world).data["data"]["id"]
    response = _client(world["admin"]).post(
        f"/api/v1/dashboard/enrollment/requests/{enrollment_id}/decision/", {"action": "approve_all"}, format="json"
    )
    assert response.status_code == 200, response.data
    assert response.data["data"]["status"] == "approved"
    assert _is_member(world["student"], world["groups"]["Dental Anatomy"])
    assert _is_member(world["student"], world["groups"]["Dental Materials"])
    assert not _is_member(world["student"], world["groups"]["Oral Biology"])
    assert Notification.objects.filter(user=world["student"], data__kind="enrollment").exists()


def test_approve_selected_rejects_the_rest_with_a_reason(world):
    data = _submit(world).data["data"]
    keep = next(item["id"] for item in data["items"] if item["subject"]["name"] == "Dental Anatomy")
    url = f"/api/v1/dashboard/enrollment/requests/{data['id']}/decision/"
    missing_reason = _client(world["admin"]).post(
        url, {"action": "approve_selected", "item_ids": [keep]}, format="json"
    )
    assert missing_reason.status_code == 400
    ok = _client(world["admin"]).post(
        url, {"action": "approve_selected", "item_ids": [keep], "reason": "Not in your study plan"}, format="json"
    )
    assert ok.data["data"]["status"] == "partially_approved"
    assert _is_member(world["student"], world["groups"]["Dental Anatomy"])
    assert not _is_member(world["student"], world["groups"]["Dental Materials"])
    mine = _rows(_client(world["student"]).get("/api/v1/enrollment/requests/"))[0]
    rejected = next(item for item in mine["items"] if item["status"] == "rejected")
    assert rejected["reason"] == "Not in your study plan"


def test_reject_all_requires_a_reason_and_adds_nobody(world):
    enrollment_id = _submit(world).data["data"]["id"]
    url = f"/api/v1/dashboard/enrollment/requests/{enrollment_id}/decision/"
    assert _client(world["admin"]).post(url, {"action": "reject_all"}, format="json").status_code == 400
    done = _client(world["admin"]).post(url, {"action": "reject_all", "reason": "Screenshot is unclear"}, format="json")
    assert done.data["data"]["status"] == "rejected"
    assert not GroupMembership.objects.filter(user=world["student"], group__subject__isnull=False).exists()


def test_a_decided_request_cannot_be_decided_again(world):
    enrollment_id = _submit(world).data["data"]["id"]
    url = f"/api/v1/dashboard/enrollment/requests/{enrollment_id}/decision/"
    assert _client(world["admin"]).post(url, {"action": "approve_all"}, format="json").status_code == 200
    assert _client(world["admin"]).post(url, {"action": "approve_all"}, format="json").status_code == 409


def test_bulk_approve_decides_each_request_on_its_own(world):
    other = User.objects.create_user(
        full_name="Omar",
        email="omar@t.test",
        phone_number="+963991000003",
        password="StrongPass123!",
        role=UserRole.STUDENT,
    )
    StudentProfile.objects.create(
        user=other,
        university=world["student"].student_profile.university,
        faculty=world["faculty"],
        academic_year=world["student"].student_profile.academic_year,
        semester=world["student"].student_profile.semester,
        student_number="2150002",
        verification_status=StudentVerificationStatus.APPROVED,
    )
    first = _submit(world).data["data"]["id"]
    second = (
        _client(other)
        .post(
            "/api/v1/enrollment/requests/",
            {"screenshot": _png(), "subject_ids": [world["subjects"]["Oral Biology"].pk]},
            format="multipart",
        )
        .data["data"]["id"]
    )
    # Decide the first one up front so the bulk call has one stale id.
    _client(world["admin"]).post(
        f"/api/v1/dashboard/enrollment/requests/{first}/decision/", {"action": "approve_all"}, format="json"
    )
    response = _client(world["admin"]).post(
        "/api/v1/dashboard/enrollment/requests/bulk-decision/",
        {"ids": [first, second], "action": "approve_all"},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["data"]["decided"] == [second]
    assert [row["id"] for row in response.data["data"]["skipped"]] == [first]
    assert _is_member(other, world["groups"]["Oral Biology"])


def test_students_cannot_use_staff_endpoints_and_staff_list_filters(world):
    enrollment_id = _submit(world).data["data"]["id"]
    assert _client(world["student"]).get("/api/v1/dashboard/enrollment/requests/").status_code == 403
    listing = _client(world["admin"]).get("/api/v1/dashboard/enrollment/requests/?status=pending&search=2150001")
    assert [row["id"] for row in _rows(listing)] == [enrollment_id]
    row = _rows(listing)[0]
    assert row["user_name"] == "Sara Student" and row["student_number"] == "2150001"
    assert "screenshot" not in row
    assert _client(world["admin"]).get("/api/v1/dashboard/enrollment/summary/").data["data"]["pending"] == 1
    assert _rows(_client(world["admin"]).get("/api/v1/dashboard/enrollment/requests/?status=approved")) == []


def test_screenshot_is_only_reachable_through_a_staff_ticket(world):
    enrollment_id = _submit(world).data["data"]["id"]
    assert (
        _client(world["student"])
        .post(f"/api/v1/dashboard/enrollment/requests/{enrollment_id}/screenshot-ticket/")
        .status_code
        == 403
    )
    ticket = _client(world["admin"]).post(f"/api/v1/dashboard/enrollment/requests/{enrollment_id}/screenshot-ticket/")
    assert ticket.status_code == 201
    path = ticket.data["data"]["preview_url"].split("testserver")[-1]
    image = _client(world["admin"]).get(path)
    assert image.status_code == 200 and image["Content-Type"] == "image/png"
    assert _client(world["student"]).get(path).status_code == 403


def test_student_cannot_join_a_subject_group_by_hand_but_can_join_others(world):
    response = _client(world["student"]).post(f"/api/v1/groups/{world['groups']['Dental Anatomy'].pk}/join/")
    assert response.status_code == 400
    assert not _is_member(world["student"], world["groups"]["Dental Anatomy"])


def test_verification_approval_joins_the_faculty_community(world):
    from apps.verification.models import VerificationRequest
    from apps.verification.services import VerificationService

    newcomer = User.objects.create_user(
        full_name="New",
        email="new@t.test",
        phone_number="+963991000009",
        password="StrongPass123!",
        role=UserRole.STUDENT,
    )
    profile = StudentProfile.objects.create(user=newcomer, university=world["student"].student_profile.university)
    request = VerificationRequest.objects.create(
        user=newcomer,
        student_profile=profile,
        university=world["faculty"].university,
        faculty=world["faculty"],
        academic_year=world["student"].student_profile.academic_year,
        semester=world["student"].student_profile.semester,
        student_number="2150077",
        card_image="verification_cards/x.png",
    )
    VerificationService.review(request, world["admin"], "approved")
    assert _is_member(newcomer, world["community"])
    assert not _is_member(newcomer, world["groups"]["Dental Anatomy"])


def test_backfill_command_is_idempotent(world):
    assert not _is_member(world["student"], world["community"])
    call_command("backfill_faculty_communities")
    call_command("backfill_faculty_communities")
    assert _is_member(world["student"], world["community"])
    assert GroupMembership.objects.filter(user=world["student"], group=world["community"]).count() == 1


def test_community_join_skips_groups_of_other_years(world):
    other_year = AcademicYear.objects.create(name="Year 2", order=2)
    pinned = Group.objects.create(
        name="Year 2 community",
        university=world["faculty"].university,
        faculty=world["faculty"],
        academic_year=other_year,
        created_by=world["admin"],
    )
    call_command("backfill_faculty_communities")
    assert _is_member(world["student"], world["community"])
    assert not _is_member(world["student"], pinned)


def test_blocked_member_stays_blocked(world):
    GroupMembership.objects.create(
        group=world["community"], user=world["student"], status=GroupMembershipStatus.BLOCKED
    )
    call_command("backfill_faculty_communities")
    assert (
        GroupMembership.objects.get(group=world["community"], user=world["student"]).status
        == GroupMembershipStatus.BLOCKED
    )


def test_request_model_enforces_one_pending(world):
    _submit(world)
    assert EnrollmentRequest.objects.filter(status=EnrollmentStatus.PENDING).count() == 1
