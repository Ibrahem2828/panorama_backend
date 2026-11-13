"""Auto-join on verification: faculty community (+ dental «السريرية 1»), level groups hidden, WhatsApp members only."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.audit.models import AuditAction, AuditLog
from apps.enrollment.services import (
    FacultyCommunityService,
    dental_clinical_groups,
    is_dental_faculty,
    normalize_arabic,
)
from apps.groups.models import Group, GroupMembership, GroupMembershipStatus
from apps.groups.services import ExternalChannelService
from apps.universities.models import AcademicYear, Faculty, Semester, Subject, University
from apps.verification.models import VerificationRequest
from apps.verification.services import VerificationService

pytestmark = pytest.mark.django_db

DENTAL = "كلية طب الأسنان"
INVITE = "https://chat.whatsapp.com/AbCdEfGhIjKlMnOp12"


@pytest.fixture
def world(settings):
    settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")
    uni = University.objects.create(name="Damascus", code="DU")
    dental = Faculty.objects.create(university=uni, name=DENTAL, code="DNT")
    law = Faculty.objects.create(university=uni, name="كلية الحقوق", code="LAW")
    year1 = AcademicYear.objects.create(name="Year 1", order=1)
    semester = Semester.objects.create(name="S1", order=1)
    admin = User.objects.create_user(
        full_name="Admin", email="ca.admin@example.test", phone_number="+963955300001", password="x" * 12, role="admin"
    )
    return {"uni": uni, "dental": dental, "law": law, "year1": year1, "semester": semester, "admin": admin}


def _group(world, name, faculty=None, **kw):
    return Group.objects.create(name=name, university=world["uni"], faculty=faculty, created_by=world["admin"], **kw)


def _student(world, faculty, n=1, status=StudentVerificationStatus.PENDING):
    user = User.objects.create_user(
        full_name=f"S{n}",
        email=f"ca.s{n}@example.test",
        phone_number=f"+96395531{n:04d}",
        password="x" * 12,
        role=UserRole.STUDENT,
        is_email_verified=True,
    )
    StudentProfile.objects.create(
        user=user,
        university=world["uni"],
        faculty=faculty,
        academic_year=world["year1"],
        semester=world["semester"],
        verification_status=status,
        student_number=f"21{n:05d}",
    )
    return user


def _approve(world, user):
    profile = user.student_profile
    request = VerificationRequest.objects.create(
        user=user,
        student_profile=profile,
        university=world["uni"],
        faculty=profile.faculty,
        academic_year=profile.academic_year,
        semester=profile.semester,
        student_number=profile.student_number,
        card_image="verification_cards/x.png",
    )
    VerificationService.review(request, world["admin"], "approved")
    user.refresh_from_db()


def _member(user, group) -> bool:
    return GroupMembership.objects.filter(user=user, group=group, status=GroupMembershipStatus.APPROVED).exists()


# -- auto-join --------------------------------------------------------------------------------------------------------


def test_approval_joins_the_community_without_any_approval_step(world):
    community = _group(world, "مجتمع طب الأسنان", world["dental"], requires_approval=True)
    user = _student(world, world["dental"])
    assert not _member(user, community)
    _approve(world, user)
    membership = GroupMembership.objects.get(user=user, group=community)
    assert membership.status == GroupMembershipStatus.APPROVED and membership.joined_at is not None
    assert AuditLog.objects.filter(action=AuditAction.COMMUNITY_AUTO_JOINED, target_id=str(community.pk)).exists()


def test_dental_student_also_joins_clinical_one(world):
    community = _group(world, "مجتمع طب الأسنان", world["dental"])
    clinical = _group(world, "السريرية 1", world["dental"])
    clinical2 = _group(world, "السريرية 2", world["dental"])
    user = _student(world, world["dental"])
    _approve(world, user)
    assert _member(user, community) and _member(user, clinical)
    assert not _member(user, clinical2)


@pytest.mark.parametrize(
    "name", ["السريرية 1", "السريريّة ١", "سريرية 1", "السريرية الأولى", "السريريه 1", "  السريرية  1 ", "السريرية-1"]
)
def test_clinical_one_is_matched_robustly(world, name):
    group = _group(world, name, world["dental"])
    assert dental_clinical_groups(world["dental"]) == [group]


@pytest.mark.parametrize("name", ["السريرية 10", "السريرية 2", "السريرية", "علم الأسنان 1", "clinical 1"])
def test_other_names_are_not_clinical_one(world, name):
    _group(world, name, world["dental"])
    assert dental_clinical_groups(world["dental"]) == []


def test_missing_clinical_group_is_skipped_silently(world, caplog):
    community = _group(world, "مجتمع طب الأسنان", world["dental"])
    user = _student(world, world["dental"])
    with caplog.at_level("INFO"):
        _approve(world, user)
    assert _member(user, community)
    assert any(r.message == "dental_clinical_group_missing" for r in caplog.records)


def test_clinical_group_rules(world):
    pinned = _group(world, "السريرية 1", world["dental"], academic_year=world["year1"])
    inactive = _group(world, "السريرية 1 ", world["dental"], is_active=False)
    elsewhere = _group(world, "السريرية 1", world["law"], academic_year=world["year1"])
    user = _student(world, world["dental"])
    _approve(world, user)
    assert _member(user, pinned)  # the named group is joined even though it is pinned to a level
    assert not _member(user, inactive) and not _member(user, elsewhere)
    law_user = _student(world, world["law"], n=2)
    _approve(world, law_user)
    assert not _member(law_user, elsewhere)  # not a dental faculty: the clinical rule never applies


def test_non_dental_faculty_gets_only_its_community(world):
    community = _group(world, "مجتمع الحقوق", world["law"])
    other = _group(world, "مجتمع طب الأسنان", world["dental"])
    user = _student(world, world["law"])
    _approve(world, user)
    assert _member(user, community) and not _member(user, other)


def test_level_groups_are_not_auto_joined(world):
    community = _group(world, "مجتمع الحقوق", world["law"])
    year_group = _group(world, "سنة أولى", world["law"], academic_year=world["year1"])
    semester_group = _group(world, "فصل أول", world["law"], semester=world["semester"])
    user = _student(world, world["law"])
    _approve(world, user)
    assert _member(user, community)
    assert not _member(user, year_group) and not _member(user, semester_group)


def test_join_is_idempotent_and_never_lifts_a_block(world):
    community = _group(world, "مجتمع طب الأسنان", world["dental"])
    clinical = _group(world, "السريرية 1", world["dental"])
    user = _student(world, world["dental"], status=StudentVerificationStatus.APPROVED)
    assert FacultyCommunityService.join(user) == 2
    assert FacultyCommunityService.join(user) == 0
    assert GroupMembership.objects.filter(user=user).count() == 2
    GroupMembership.objects.filter(user=user, group=clinical).update(status=GroupMembershipStatus.BLOCKED)
    assert FacultyCommunityService.join(user) == 0
    assert GroupMembership.objects.get(user=user, group=clinical).status == GroupMembershipStatus.BLOCKED
    assert _member(user, community)


def test_left_member_comes_back_on_next_verification_join(world):
    community = _group(world, "مجتمع الحقوق", world["law"])
    user = _student(world, world["law"], status=StudentVerificationStatus.APPROVED)
    FacultyCommunityService.join(user)
    GroupMembership.objects.filter(user=user, group=community).update(status=GroupMembershipStatus.LEFT)
    assert FacultyCommunityService.join(user) == 1


def test_unverified_and_non_students_are_never_joined(world):
    _group(world, "مجتمع الحقوق", world["law"])
    pending = _student(world, world["law"])
    assert FacultyCommunityService.join(pending) == 0
    assert FacultyCommunityService.join(world["admin"]) == 0


def test_backfill_command_uses_the_same_rules(world):
    community = _group(world, "مجتمع طب الأسنان", world["dental"])
    clinical = _group(world, "السريرية 1", world["dental"])
    user = _student(world, world["dental"], status=StudentVerificationStatus.APPROVED)
    call_command("backfill_faculty_communities", "--dry-run")
    assert not GroupMembership.objects.filter(user=user).exists()
    call_command("backfill_faculty_communities")
    call_command("backfill_faculty_communities")
    assert _member(user, community) and _member(user, clinical)
    assert GroupMembership.objects.filter(user=user).count() == 2


def test_dental_faculty_detection_and_normalization():
    assert is_dental_faculty(Faculty(name="كلية طب الأسنان", code=""))
    assert is_dental_faculty(Faculty(name="كلية طب الاسنان", code=""))
    assert is_dental_faculty(Faculty(name="Faculty of Dentistry", code=""))
    assert is_dental_faculty(Faculty(name="Faculty X", code="DDS"))
    assert not is_dental_faculty(Faculty(name="كلية الطب البشري", code="MED"))
    assert not is_dental_faculty(None)
    assert normalize_arabic("السريرّيـة ١") == "السريريه 1"


# -- level groups are hidden from students ----------------------------------------------------------------------------


def _client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


def test_level_groups_are_not_listed_or_retrievable_for_non_members(world):
    year_group = _group(world, "سنة أولى", world["law"], academic_year=world["year1"])
    subject = Subject.objects.create(
        faculty=world["law"], academic_year=world["year1"], semester=world["semester"], name="Civil law", code="CL1"
    )
    subject_group = _group(world, "Civil law group", world["law"], subject=subject)
    user = _student(world, world["law"], status=StudentVerificationStatus.APPROVED)
    client = _client(user)
    for url in ("/api/v1/groups/available/", "/api/v1/groups/"):
        ids = {row["id"] for row in client.get(url).data["data"]["results"]}
        assert year_group.id not in ids, url
        assert subject_group.id in ids, url  # subject enrollment groups are untouched
    assert client.get(f"/api/v1/groups/{year_group.id}/").status_code == 403
    assert client.get(f"/api/v1/groups/{subject_group.id}/").status_code == 200
    # existing members keep the group (data is kept, only the offering is gone)
    GroupMembership.objects.create(group=year_group, user=user, status=GroupMembershipStatus.APPROVED)
    assert year_group.id in {row["id"] for row in client.get("/api/v1/groups/").data["data"]["results"]}
    assert year_group.id in {row["id"] for row in client.get("/api/v1/groups/my/").data["data"]["results"]}
    assert client.get(f"/api/v1/groups/{year_group.id}/").status_code == 200


def test_students_cannot_join_any_group_by_themselves(world):
    year_group = _group(world, "سنة أولى", world["law"], academic_year=world["year1"])
    user = _student(world, world["law"], status=StudentVerificationStatus.APPROVED)
    response = _client(user).post(f"/api/v1/groups/{year_group.id}/join/")
    assert response.status_code == 403 and response.data["code"] == "GROUP_JOIN_ENROLLMENT_ONLY"


# -- WhatsApp links reach active members only -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    [
        GroupMembershipStatus.PENDING,
        GroupMembershipStatus.REJECTED,
        GroupMembershipStatus.BLOCKED,
        GroupMembershipStatus.LEFT,
        GroupMembershipStatus.TERM_ENDED,
    ],
)
def test_only_approved_members_get_the_whatsapp_link(world, status):
    group = _group(world, "مجتمع الحقوق", world["law"])
    ExternalChannelService.set_whatsapp_channel(group, INVITE, world["admin"])
    user = _student(world, world["law"], status=StudentVerificationStatus.APPROVED)
    client = _client(user)
    assert client.post(f"/api/v1/groups/{group.id}/whatsapp-launch/").status_code == 403  # never a member
    GroupMembership.objects.create(group=group, user=user, status=status)
    refused = client.post(f"/api/v1/groups/{group.id}/whatsapp-launch/")
    assert refused.status_code == 403 and "whatsapp" not in refused.content.decode().lower().replace(
        "whatsapp channel", ""
    )
    GroupMembership.objects.filter(group=group, user=user).update(status=GroupMembershipStatus.APPROVED)
    granted = client.post(f"/api/v1/groups/{group.id}/whatsapp-launch/")
    assert granted.status_code == 200 and granted.data["data"]["app_url"].startswith("whatsapp://")


def test_a_suspended_student_loses_the_link_even_as_a_member(world):
    group = _group(world, "مجتمع الحقوق", world["law"])
    ExternalChannelService.set_whatsapp_channel(group, INVITE, world["admin"])
    user = _student(world, world["law"], status=StudentVerificationStatus.APPROVED)
    GroupMembership.objects.create(group=group, user=user, status=GroupMembershipStatus.APPROVED)
    StudentProfile.objects.filter(user=user).update(verification_status=StudentVerificationStatus.SUSPENDED)
    assert _client(User.objects.get(pk=user.pk)).post(f"/api/v1/groups/{group.id}/whatsapp-launch/").status_code == 403


def test_no_student_endpoint_serialises_the_invite(world):
    group = _group(world, "مجتمع الحقوق", world["law"])
    ExternalChannelService.set_whatsapp_channel(group, INVITE, world["admin"])
    user = _student(world, world["law"], status=StudentVerificationStatus.APPROVED)
    GroupMembership.objects.create(group=group, user=user, status=GroupMembershipStatus.APPROVED)
    client = _client(user)
    for url in ("/api/v1/groups/", "/api/v1/groups/my/", "/api/v1/groups/available/", f"/api/v1/groups/{group.id}/"):
        body = client.get(url).content.decode()
        assert "chat.whatsapp.com" not in body and "AbCdEfGhIjKlMnOp12" not in body, url
    assert client.get(f"/api/v1/groups/{group.id}/").data["data"]["has_whatsapp_channel"] is True
