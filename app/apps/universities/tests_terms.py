"""Current term, end of term (preview / run / undo / WhatsApp list) and what ``term_ended`` may no longer do."""

from __future__ import annotations

import io

import pytest
from cryptography.fernet import Fernet
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.audit.models import AuditAction, AuditLog
from apps.chat.models import Message
from apps.chat.notify import ChatNotificationService
from apps.chat.services import ChatPermissionService
from apps.enrollment.models import EnrollmentRequest
from apps.enrollment.services import EnrollmentService
from apps.files.models import FileResource, FileVisibility
from apps.files.services import accessible_files_for_user, user_can_access_file
from apps.groups.models import (
    ExternalChannelAccessTicket,
    Group,
    GroupExternalChannel,
    GroupMembership,
    GroupMembershipRole,
    GroupMembershipStatus,
)
from apps.groups.services import ExternalChannelService
from apps.universities.models import AcademicYear, CurrentTerm, Faculty, Semester, Subject, TermRollover, University
from apps.universities.terms import TermConflict, end_term, preview_end_term

INVITE = "https://chat.whatsapp.com/AbCdEfGhIjKlMnOp12"
BASE = "/api/v1/dashboard/academic/end-term"


def _client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


def _user(email, phone, role=UserRole.STUDENT, name=None) -> User:
    return User.objects.create_user(
        full_name=name or email, email=email, phone_number=phone, password="StrongPass123!", role=role
    )


def _student(world, n: int, name=None) -> User:
    user = _user(f"s{n}@t.test", f"+96391000{n:04d}", name=name or f"Student {n}")
    StudentProfile.objects.create(
        user=user,
        university=world["uni"],
        faculty=world["faculty"],
        academic_year=world["year"],
        semester=world["sem1"],
        student_number=f"21{n:05d}",
        verification_status=StudentVerificationStatus.APPROVED,
    )
    return user


def _member(group, user, role=GroupMembershipRole.MEMBER, status=GroupMembershipStatus.APPROVED):
    return GroupMembership.objects.create(group=group, user=user, role=role, status=status, joined_at=timezone.now())


@pytest.fixture
def world(db, settings):
    settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")
    uni = University.objects.create(name="Uni", code="U1")
    faculty = Faculty.objects.create(university=uni, name="Dentistry", code="DEN")
    year = AcademicYear.objects.create(name="Year 1", order=1)
    sem1 = Semester.objects.create(name="First semester", order=1)
    sem2 = Semester.objects.create(name="Second semester", order=2)
    admin = _user("admin@t.test", "+963900000001", UserRole.ADMIN, "Admin")
    it = _user("it@t.test", "+963900000002", UserRole.IT_SUPPORT, "IT")
    printer = _user("print@t.test", "+963900000003", UserRole.PRINT_STAFF, "Printer")
    subject_a = Subject.objects.create(faculty=faculty, academic_year=year, semester=sem1, name="Anatomy", code="A1")
    subject_b = Subject.objects.create(faculty=faculty, academic_year=year, semester=sem2, name="Biology", code="B1")
    ga = Group.objects.create(name="Anatomy group", university=uni, faculty=faculty, subject=subject_a, created_by=admin)
    gb = Group.objects.create(name="Biology group", university=uni, faculty=faculty, subject=subject_b, created_by=admin)
    community = Group.objects.create(name="Faculty community", university=uni, faculty=faculty, created_by=admin)
    ExternalChannelService.set_whatsapp_channel(ga, INVITE, admin)
    s1, s2, s3 = (_student({"uni": uni, "faculty": faculty, "year": year, "sem1": sem1}, n) for n in (1, 2, 3))
    return {
        "uni": uni,
        "faculty": faculty,
        "year": year,
        "sem1": sem1,
        "sem2": sem2,
        "admin": admin,
        "it": it,
        "printer": printer,
        "subject_a": subject_a,
        "subject_b": subject_b,
        "ga": ga,
        "gb": gb,
        "community": community,
        "s1": s1,
        "s2": s2,
        "s3": s3,
    }


@pytest.fixture
def populated(world):
    """s1+s2 in the anatomy group, s2 also in biology, everyone in the community; staff and a group admin too."""

    w = world
    for student in (w["s1"], w["s2"]):
        _member(w["ga"], student)
    _member(w["gb"], w["s2"])
    for student in (w["s1"], w["s2"], w["s3"]):
        _member(w["community"], student)
    w["group_admin"] = _student(w, 9, name="Group Admin Student")
    _member(w["ga"], w["group_admin"], role=GroupMembershipRole.GROUP_ADMIN)
    w["moderator"] = _student(w, 8, name="Moderator Student")
    _member(w["ga"], w["moderator"], role=GroupMembershipRole.MODERATOR)
    _member(w["ga"], w["printer"])  # a staff account holding a plain "member" row
    _member(w["ga"], w["s3"], status=GroupMembershipStatus.PENDING)
    return w


def _end(client, w, **overrides):
    body = {"confirm": True, "next_semester": w["sem2"].pk, "label": "Spring", "disable_whatsapp_links": True}
    body.update(overrides)
    return client.post(f"{BASE}/", body, format="json")


# -- current term -------------------------------------------------------------------------------------------------------


def test_current_term_falls_back_to_first_semester_then_follows_the_setting(world):
    w = world
    student = _client(w["s1"])
    data = student.get("/api/v1/academic/current-term/").data["data"]
    assert data["semester"] == {"id": w["sem1"].pk, "name": "First semester", "order": 1}
    assert data["other_semester"]["id"] == w["sem2"].pk
    assert data["term_started_at"] is None and data["term_label"] == ""

    response = _client(w["admin"]).put(
        "/api/v1/dashboard/academic/current-term/", {"semester": w["sem2"].pk, "label": "  Spring  2027 "}, format="json"
    )
    assert response.status_code == 200
    data = student.get("/api/v1/academic/current-term/").data["data"]
    assert data["semester"]["id"] == w["sem2"].pk
    assert data["other_semester"]["id"] == w["sem1"].pk
    assert data["term_label"] == "Spring 2027"
    assert data["term_started_at"] is not None
    assert AuditLog.objects.filter(action=AuditAction.CURRENT_TERM_CHANGED, actor=w["admin"]).count() == 1
    assert CurrentTerm.objects.count() == 1


def test_current_term_requires_authentication(world):
    assert APIClient().get("/api/v1/academic/current-term/").status_code in (401, 403)


def test_dashboard_current_term_permissions_and_validation(world):
    w = world
    url = "/api/v1/dashboard/academic/current-term/"
    assert _client(w["s1"]).get(url).status_code == 403
    assert _client(w["printer"]).get(url).status_code == 403
    assert _client(w["printer"]).put(url, {"semester": w["sem1"].pk}, format="json").status_code == 403
    assert _client(w["admin"]).get(url).status_code == 200
    assert _client(w["admin"]).put(url, {"semester": 9999}, format="json").status_code == 400
    w["sem2"].is_active = False
    w["sem2"].save()
    assert _client(w["admin"]).put(url, {"semester": w["sem2"].pk}, format="json").status_code == 400
    assert not CurrentTerm.objects.exists()


# -- preview ------------------------------------------------------------------------------------------------------------


def test_preview_lists_only_what_the_action_would_remove(populated):
    w = populated
    response = _client(w["admin"]).post(f"{BASE}/preview/")
    assert response.status_code == 200
    data = response.data["data"]
    # anatomy: s1, s2 (not the group admin, the moderator, the staff account, or the pending request); biology: s2.
    assert data["total_memberships"] == 3
    assert data["total_students"] == 2
    by_group = {row["group_id"]: row for row in data["groups"]}
    assert set(by_group) == {w["ga"].pk, w["gb"].pk}
    assert by_group[w["ga"].pk]["members_to_remove"] == 2
    assert by_group[w["ga"].pk]["whatsapp_active"] is True
    assert by_group[w["ga"].pk]["subject"] == "Anatomy"
    assert by_group[w["gb"].pk]["members_to_remove"] == 1
    assert by_group[w["gb"].pk]["whatsapp_active"] is False
    assert w["community"].pk not in by_group
    # the preview changes nothing
    assert not GroupMembership.objects.filter(status=GroupMembershipStatus.TERM_ENDED).exists()


def test_preview_equals_what_end_term_removes(populated):
    w = populated
    preview = preview_end_term()
    rollover = end_term(actor=w["admin"], next_semester_id=w["sem2"].pk, label="Spring")
    assert rollover.memberships_removed == preview["total_memberships"]
    assert rollover.students_affected == preview["total_students"]
    assert [g["group_id"] for g in rollover.snapshot] == [g["group_id"] for g in preview["groups"]]
    assert GroupMembership.objects.filter(status=GroupMembershipStatus.TERM_ENDED).count() == 3


def test_preview_with_nothing_to_remove_is_empty(world):
    data = _client(world["admin"]).post(f"{BASE}/preview/").data["data"]
    assert data == {"groups": [], "total_memberships": 0, "total_students": 0}


@pytest.mark.parametrize("who", ["s1", "printer"])
def test_term_endpoints_deny_students_and_staff_without_academic_manage(populated, who):
    w = populated
    client = _client(w[who])
    assert client.post(f"{BASE}/preview/").status_code == 403
    assert client.get(f"{BASE}/").status_code == 403
    assert _end(client, w).status_code == 403
    assert client.get(f"{BASE}/1/whatsapp-list/").status_code == 403
    assert client.post(f"{BASE}/1/undo/").status_code == 403
    assert not GroupMembership.objects.filter(status=GroupMembershipStatus.TERM_ENDED).exists()


def test_term_endpoints_need_login(populated):
    client = APIClient()
    assert client.post(f"{BASE}/preview/").status_code in (401, 403)
    assert client.post(f"{BASE}/", {"confirm": True, "next_semester": 1}, format="json").status_code in (401, 403)


# -- ending the term ----------------------------------------------------------------------------------------------------


def test_end_term_requires_explicit_confirmation(populated):
    w = populated
    client = _client(w["admin"])
    assert client.post(f"{BASE}/", {"next_semester": w["sem2"].pk}, format="json").status_code == 400
    assert _end(client, w, confirm=False).status_code == 400
    assert _end(client, w, next_semester=99999).status_code == 400
    assert not TermRollover.objects.exists()
    assert not GroupMembership.objects.filter(status=GroupMembershipStatus.TERM_ENDED).exists()


def test_end_term_removes_only_subject_group_students_and_keeps_every_record(populated):
    w = populated
    response = _end(_client(w["admin"]), w)
    assert response.status_code == 200, response.data
    data = response.data["data"]
    assert data["removed"] == 3
    assert {g["group_id"] for g in data["groups"]} == {w["ga"].pk, w["gb"].pk}

    ended = GroupMembership.objects.filter(status=GroupMembershipStatus.TERM_ENDED)
    assert ended.count() == 3  # rows are kept, nothing deleted
    assert all(m.ended_at is not None and m.rollover_id == data["rollover_id"] for m in ended)
    assert {(m.group_id, m.user_id) for m in ended} == {
        (w["ga"].pk, w["s1"].pk),
        (w["ga"].pk, w["s2"].pk),
        (w["gb"].pk, w["s2"].pk),
    }
    # untouched: faculty community, group admin, moderator, staff row, the pending request
    for user in (w["s1"], w["s2"], w["s3"]):
        assert GroupMembership.objects.get(group=w["community"], user=user).status == GroupMembershipStatus.APPROVED
    assert GroupMembership.objects.get(group=w["ga"], user=w["group_admin"]).status == GroupMembershipStatus.APPROVED
    assert GroupMembership.objects.get(group=w["ga"], user=w["moderator"]).status == GroupMembershipStatus.APPROVED
    assert GroupMembership.objects.get(group=w["ga"], user=w["printer"]).status == GroupMembershipStatus.APPROVED
    assert GroupMembership.objects.get(group=w["ga"], user=w["s3"]).status == GroupMembershipStatus.PENDING

    # current term moved, every student asked to confirm, rollover recorded, audited
    assert CurrentTerm.objects.get().semester_id == w["sem2"].pk
    assert CurrentTerm.objects.get().label == "Spring"
    assert StudentProfile.objects.filter(needs_term_confirmation=True).count() == StudentProfile.objects.count() > 0
    rollover = TermRollover.objects.get()
    assert rollover.performed_by == w["admin"] and rollover.memberships_removed == 3 and rollover.students_affected == 2
    log = AuditLog.objects.get(action=AuditAction.TERM_ENDED)
    assert log.actor == w["admin"] and log.new_value["memberships_removed"] == 3


def test_end_term_disables_whatsapp_links_and_revokes_open_tickets(populated):
    w = populated
    channel = GroupExternalChannel.objects.get(group=w["ga"])
    ticket = ExternalChannelAccessTicket.issue(channel, w["s1"])
    assert ticket.is_valid
    data = _end(_client(w["admin"]), w).data["data"]
    channel.refresh_from_db()
    ticket.refresh_from_db()
    assert channel.is_active is False
    assert ticket.is_valid is False
    assert next(g for g in data["groups"] if g["group_id"] == w["ga"].pk)["whatsapp_disabled"] is True


def test_end_term_can_leave_whatsapp_links_alone(populated):
    w = populated
    _end(_client(w["admin"]), w, disable_whatsapp_links=False)
    assert GroupExternalChannel.objects.get(group=w["ga"]).is_active is True


def test_a_second_confirm_does_not_process_twice(populated):
    w = populated
    client = _client(w["admin"])
    assert _end(client, w).status_code == 200
    again = _end(client, w)
    assert again.status_code == 409
    assert TermRollover.objects.count() == 1
    assert AuditLog.objects.filter(action=AuditAction.TERM_ENDED).count() == 1
    with pytest.raises(TermConflict):
        end_term(actor=w["admin"], next_semester_id=w["sem2"].pk, label="Spring")


def test_same_idempotency_key_replays_instead_of_repeating(populated):
    w = populated
    client = _client(w["admin"])
    body = {"confirm": True, "next_semester": w["sem2"].pk, "label": "Spring", "disable_whatsapp_links": True}
    first = client.post(f"{BASE}/", body, format="json", HTTP_IDEMPOTENCY_KEY="end-term-key-0001")
    second = client.post(f"{BASE}/", body, format="json", HTTP_IDEMPOTENCY_KEY="end-term-key-0001")
    assert first.status_code == second.status_code == 200
    assert first.data["data"]["rollover_id"] == second.data["data"]["rollover_id"]
    assert TermRollover.objects.count() == 1


def test_end_term_is_atomic(populated, monkeypatch):
    w = populated

    def boom(*args, **kwargs):
        raise RuntimeError("audit trail unavailable")

    monkeypatch.setattr("apps.universities.terms.AuditLogService.log", boom)
    with pytest.raises(RuntimeError):
        end_term(actor=w["admin"], next_semester_id=w["sem2"].pk, label="Spring")
    assert not TermRollover.objects.exists()
    assert not GroupMembership.objects.filter(status=GroupMembershipStatus.TERM_ENDED).exists()
    assert GroupExternalChannel.objects.get(group=w["ga"]).is_active is True
    assert not CurrentTerm.objects.exists()
    assert not StudentProfile.objects.filter(needs_term_confirmation=True).exists()


def test_it_support_may_end_a_term_and_the_history_lists_it(populated):
    w = populated
    assert _end(_client(w["it"]), w).status_code == 200
    rows = _client(w["admin"]).get(f"{BASE}/").data["data"]
    assert len(rows) == 1
    assert rows[0]["memberships_removed"] == 3 and rows[0]["can_undo"] is True
    assert rows[0]["performed_by"]["id"] == w["it"].pk and rows[0]["is_undone"] is False


# -- WhatsApp list ------------------------------------------------------------------------------------------------------


def test_whatsapp_list_json_and_csv(populated):
    w = populated
    w["s1"].full_name = "=SUM(1+1)"
    w["s1"].save()
    rollover_id = _end(_client(w["admin"]), w).data["data"]["rollover_id"]
    client = _client(w["admin"])

    response = client.get(f"{BASE}/{rollover_id}/whatsapp-list/")
    assert response.status_code == 200
    groups = {g["group_id"]: g for g in response.data["data"]["groups"]}
    assert set(groups) == {w["ga"].pk, w["gb"].pk}
    assert {m["phone_number"] for m in groups[w["ga"].pk]["members"]} == {w["s1"].phone_number, w["s2"].phone_number}
    assert [m["full_name"] for m in groups[w["gb"].pk]["members"]] == ["Student 2"]

    csv_response = client.get(f"{BASE}/{rollover_id}/whatsapp-list/?format=csv")
    assert csv_response.status_code == 200
    assert csv_response["Content-Type"].startswith("text/csv")
    body = csv_response.content.decode("utf-8")
    assert body.startswith("﻿")
    lines = body.lstrip("﻿").splitlines()
    assert lines[0] == "group,name,phone"
    assert "Anatomy group,'=SUM(1+1)," + w["s1"].phone_number in lines  # formula neutralised, phone left intact
    assert len(lines) == 1 + 3
    assert AuditLog.objects.filter(action=AuditAction.TERM_WHATSAPP_LIST_EXPORTED, actor=w["admin"]).count() == 2


def test_whatsapp_list_unknown_rollover_and_after_undo(populated):
    w = populated
    client = _client(w["admin"])
    assert client.get(f"{BASE}/999/whatsapp-list/").status_code == 404
    rollover_id = _end(client, w).data["data"]["rollover_id"]
    assert client.post(f"{BASE}/{rollover_id}/undo/").status_code == 200
    assert client.get(f"{BASE}/{rollover_id}/whatsapp-list/").status_code == 409


def test_whatsapp_list_leaves_out_students_who_came_back(populated):
    w = populated
    rollover_id = _end(_client(w["admin"]), w).data["data"]["rollover_id"]
    membership = GroupMembership.objects.get(group=w["ga"], user=w["s1"])
    membership.approve(w["admin"])
    groups = _client(w["admin"]).get(f"{BASE}/{rollover_id}/whatsapp-list/").data["data"]["groups"]
    anatomy = next(g for g in groups if g["group_id"] == w["ga"].pk)
    assert [m["full_name"] for m in anatomy["members"]] == ["Student 2"]


# -- undo ---------------------------------------------------------------------------------------------------------------


def test_undo_restores_memberships_term_and_flags_but_not_whatsapp(populated):
    w = populated
    client = _client(w["admin"])
    rollover_id = _end(client, w).data["data"]["rollover_id"]
    response = client.post(f"{BASE}/{rollover_id}/undo/")
    assert response.status_code == 200 and response.data["data"]["restored"] == 3
    assert not GroupMembership.objects.filter(status=GroupMembershipStatus.TERM_ENDED).exists()
    restored = GroupMembership.objects.get(group=w["ga"], user=w["s1"])
    assert restored.status == GroupMembershipStatus.APPROVED and restored.ended_at is None and restored.rollover is None
    assert GroupExternalChannel.objects.get(group=w["ga"]).is_active is False  # links stay disabled
    assert CurrentTerm.objects.count() == 0  # there was no current term before this run
    assert not StudentProfile.objects.filter(needs_term_confirmation=True).exists()
    assert TermRollover.objects.get().undone_by == w["admin"]
    assert AuditLog.objects.filter(action=AuditAction.TERM_ROLLOVER_UNDONE).count() == 1
    rows = client.get(f"{BASE}/").data["data"]
    assert rows[0]["is_undone"] is True and rows[0]["can_undo"] is False


def test_undo_puts_the_previous_term_back(populated):
    w = populated
    client = _client(w["admin"])
    client.put("/api/v1/dashboard/academic/current-term/", {"semester": w["sem1"].pk, "label": "Fall"}, format="json")
    rollover_id = _end(client, w).data["data"]["rollover_id"]
    client.post(f"{BASE}/{rollover_id}/undo/")
    term = CurrentTerm.objects.get()
    assert term.semester_id == w["sem1"].pk and term.label == "Fall"


def test_undo_is_once_only_and_latest_only(populated):
    w = populated
    client = _client(w["admin"])
    first = _end(client, w).data["data"]["rollover_id"]
    second = _end(client, w, next_semester=w["sem1"].pk, label="Fall 2").data["data"]["rollover_id"]
    assert client.post(f"{BASE}/{first}/undo/").status_code == 409  # not the latest
    assert client.post(f"{BASE}/{second}/undo/").status_code == 200
    assert client.post(f"{BASE}/{second}/undo/").status_code == 409  # already undone
    assert client.post(f"{BASE}/9999/undo/").status_code == 404


def test_undo_does_not_touch_students_who_were_blocked_or_re_enrolled_since(populated):
    w = populated
    client = _client(w["admin"])
    rollover_id = _end(client, w).data["data"]["rollover_id"]
    blocked = GroupMembership.objects.get(group=w["ga"], user=w["s1"])
    blocked.status = GroupMembershipStatus.BLOCKED
    blocked.save()
    client.post(f"{BASE}/{rollover_id}/undo/")
    blocked.refresh_from_db()
    assert blocked.status == GroupMembershipStatus.BLOCKED
    assert GroupMembership.objects.get(group=w["ga"], user=w["s2"]).status == GroupMembershipStatus.APPROVED


# -- term_ended means no access -----------------------------------------------------------------------------------------


def test_term_ended_students_lose_chat_files_whatsapp_groups_and_push(populated):
    w = populated
    s1, s2 = w["s1"], w["s2"]
    group = w["ga"]
    private_file = FileResource.objects.create(
        title="Anatomy notes",
        file=SimpleUploadedFile("a.pdf", b"%PDF-1.4"),
        uploaded_by=w["admin"],
        visibility=FileVisibility.GROUP_ONLY,
        group=group,
    )
    message = Message.objects.create(group=group, sender=w["group_admin"], content="hello")
    # before: s1 has everything
    assert ChatPermissionService.can_access_group_chat(s1, group)
    assert user_can_access_file(s1, private_file)
    assert _client(s1).get(f"/api/v1/groups/{group.pk}/messages/").status_code == 200
    assert group.pk in [g["id"] for g in _rows(_client(s1).get("/api/v1/groups/my/"))]
    assert s1 in ChatNotificationService.recipients(message)

    _end(_client(w["admin"]), w, disable_whatsapp_links=False)

    # chat: read and write are refused
    assert not ChatPermissionService.can_access_group_chat(s1, group)
    assert not ChatPermissionService.can_send_message(s1, group)
    assert _client(s1).get(f"/api/v1/groups/{group.pk}/messages/").status_code == 403
    assert _client(s1).post(f"/api/v1/groups/{group.pk}/messages/", {"content": "x"}, format="json").status_code == 403
    # files
    assert not user_can_access_file(s1, private_file)
    assert private_file.pk not in [f.pk for f in accessible_files_for_user(s1)]
    # my groups and the group endpoint's membership view
    assert group.pk not in [g["id"] for g in _rows(_client(s1).get("/api/v1/groups/my/"))]
    assert w["community"].pk in [g["id"] for g in _rows(_client(s1).get("/api/v1/groups/my/"))]
    # WhatsApp (links left on here, so only the membership check is what refuses)
    assert _client(s1).post(f"/api/v1/groups/{group.pk}/whatsapp-launch/").status_code == 403
    assert not ExternalChannelService.user_can_open(s1, GroupExternalChannel.objects.get(group=group))
    # push fan-out / receipts do not count them any more
    assert s1 not in ChatNotificationService.recipients(message)
    assert s2 not in ChatNotificationService.recipients(message)
    # joining a subject group by hand is still impossible (it needs an enrollment)
    assert _client(s1).post(f"/api/v1/groups/{group.pk}/join/").status_code == 400
    # non-subject members still have their access
    assert ChatPermissionService.can_access_group_chat(s1, w["community"])


def _rows(response):
    data = response.data["data"]
    return data["results"] if isinstance(data, dict) and "results" in data else data


def test_group_admin_and_staff_keep_chat_access(populated):
    w = populated
    _end(_client(w["admin"]), w)
    assert ChatPermissionService.can_access_group_chat(w["group_admin"], w["ga"])
    assert ChatPermissionService.can_access_group_chat(w["moderator"], w["ga"])
    assert ChatPermissionService.can_access_group_chat(w["admin"], w["ga"])


# -- coming back --------------------------------------------------------------------------------------------------------


def _png() -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(buffer, "PNG")
    return SimpleUploadedFile("shot.png", buffer.getvalue(), content_type="image/png")


def test_enrollment_approval_brings_a_term_ended_student_back(populated):
    w = populated
    _end(_client(w["admin"]), w)
    membership = GroupMembership.objects.get(group=w["ga"], user=w["s1"])
    assert membership.status == GroupMembershipStatus.TERM_ENDED

    enrollment = EnrollmentService.submit(user=w["s1"], screenshot=_png(), subject_ids=[w["subject_a"].pk])
    EnrollmentService.decide(enrollment, w["admin"], approve_item_ids=None)

    membership.refresh_from_db()
    assert membership.status == GroupMembershipStatus.APPROVED
    assert membership.ended_at is None and membership.rollover_id is None
    assert GroupMembership.objects.filter(group=w["ga"], user=w["s1"]).count() == 1
    assert ChatPermissionService.can_access_group_chat(w["s1"], w["ga"])
    assert EnrollmentRequest.objects.filter(user=w["s1"]).count() == 1


def test_a_dashboard_approval_also_lifts_term_ended(populated):
    w = populated
    _end(_client(w["admin"]), w)
    membership = GroupMembership.objects.get(group=w["ga"], user=w["s1"])
    response = _client(w["admin"]).post(f"/api/v1/dashboard/group-memberships/{membership.pk}/approve/")
    assert response.status_code == 200
    membership.refresh_from_db()
    assert membership.status == GroupMembershipStatus.APPROVED and membership.rollover_id is None


# -- subject search -----------------------------------------------------------------------------------------------------


def test_subject_search_covers_any_term_and_lists_the_current_one_first(world):
    w = world
    Subject.objects.create(faculty=w["faculty"], academic_year=w["year"], semester=w["sem2"], name="Anatomy II", code="A2")
    Subject.objects.create(faculty=w["faculty"], academic_year=w["year"], semester=w["sem1"], name="Anatomy Lab", code="A3")
    client = _client(w["s1"])
    rows = client.get("/api/v1/enrollment/subjects/?q=Anatomy").data["data"]
    assert [(r["name"], r["in_current_term"]) for r in rows] == [
        ("Anatomy", True),
        ("Anatomy Lab", True),
        ("Anatomy II", False),
    ]
    # after the admin moves to the second semester the order and flags follow
    _client(w["admin"]).put("/api/v1/dashboard/academic/current-term/", {"semester": w["sem2"].pk}, format="json")
    rows = client.get("/api/v1/enrollment/subjects/?q=Anatomy").data["data"]
    assert [(r["name"], r["in_current_term"]) for r in rows] == [
        ("Anatomy II", True),
        ("Anatomy", False),
        ("Anatomy Lab", False),
    ]
    # a student of year 1 / semester 1 can still find and enrol in a later year's subject
    year3 = AcademicYear.objects.create(name="Year 3", order=3)
    Subject.objects.create(faculty=w["faculty"], academic_year=year3, semester=w["sem1"], name="Anatomy Advanced", code="A4")
    names = [r["name"] for r in client.get("/api/v1/enrollment/subjects/?q=Advanced").data["data"]]
    assert names == ["Anatomy Advanced"]


# -- student confirmation -----------------------------------------------------------------------------------------------


def test_profile_shows_the_flag_and_the_term(populated):
    w = populated
    client = _client(w["s1"])
    before = client.get("/api/v1/students/me/profile/").data["data"]
    assert before["needs_term_confirmation"] is False
    assert before["current_term"]["semester"]["id"] == w["sem1"].pk
    _end(_client(w["admin"]), w)
    after = _client(User.objects.get(pk=w["s1"].pk)).get("/api/v1/students/me/profile/").data["data"]
    assert after["needs_term_confirmation"] is True
    assert after["current_term"]["semester"]["id"] == w["sem2"].pk
    assert after["current_term"]["term_label"] == "Spring"


def test_student_can_not_write_the_flag_through_the_profile_patch(populated):
    w = populated
    client = _client(w["s1"])
    client.patch("/api/v1/students/me/profile/", {"needs_term_confirmation": True}, format="json")
    assert StudentProfile.objects.get(user=w["s1"]).needs_term_confirmation is False


def test_term_confirmation_promoted_updates_year_and_semester_and_is_audited(populated):
    w = populated
    year2 = AcademicYear.objects.create(name="Year 2", order=2)
    _end(_client(w["admin"]), w)
    response = _client(w["s1"]).post(
        "/api/v1/students/me/term-confirmation/",
        {"promoted": True, "academic_year": year2.pk, "semester": w["sem2"].pk},
        format="json",
    )
    assert response.status_code == 200, response.data
    profile = StudentProfile.objects.get(user=w["s1"])
    assert profile.academic_year_id == year2.pk and profile.semester_id == w["sem2"].pk
    assert profile.needs_term_confirmation is False
    assert response.data["data"]["needs_term_confirmation"] is False
    log = AuditLog.objects.get(action=AuditAction.STUDENT_TERM_CONFIRMED, actor=w["s1"])
    assert log.old_value == {"academic_year": w["year"].pk, "semester": w["sem1"].pk}
    assert log.new_value["promoted"] is True
    # the other students are still asked
    assert StudentProfile.objects.get(user=w["s2"]).needs_term_confirmation is True


def test_term_confirmation_not_promoted_clears_the_flag_and_keeps_the_level(populated):
    w = populated
    _end(_client(w["admin"]), w)
    response = _client(w["s1"]).post("/api/v1/students/me/term-confirmation/", {"promoted": False}, format="json")
    assert response.status_code == 200
    profile = StudentProfile.objects.get(user=w["s1"])
    assert profile.academic_year_id == w["year"].pk and profile.semester_id == w["sem1"].pk
    assert profile.needs_term_confirmation is False


def test_term_confirmation_validation_and_guards(populated):
    w = populated
    url = "/api/v1/students/me/term-confirmation/"
    client = _client(w["s1"])
    # nothing pending: cannot be used to change the level
    assert client.post(url, {"promoted": True, "academic_year": w["year"].pk}, format="json").status_code == 409
    _end(_client(w["admin"]), w)
    assert client.post(url, {}, format="json").status_code == 400
    assert client.post(url, {"promoted": True}, format="json").status_code == 400
    assert client.post(url, {"promoted": True, "academic_year": 99999}, format="json").status_code == 400
    assert client.post(url, {"promoted": True, "semester": 99999}, format="json").status_code == 400
    assert StudentProfile.objects.get(user=w["s1"]).needs_term_confirmation is True
    # only students; staff and anonymous are refused
    assert _client(w["admin"]).post(url, {"promoted": False}, format="json").status_code == 403
    assert APIClient().post(url, {"promoted": False}, format="json").status_code in (401, 403)
    # a second answer after the first is refused
    assert client.post(url, {"promoted": False}, format="json").status_code == 200
    assert client.post(url, {"promoted": True, "academic_year": w["year"].pk}, format="json").status_code == 409
