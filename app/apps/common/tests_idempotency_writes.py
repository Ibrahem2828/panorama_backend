"""``Idempotency-Key`` behaviour on the student-facing create endpoints beyond support tickets."""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.chat.models import Message, MessageReport
from apps.feedback.models import AppFeedback, FeedbackContext, FeedbackKind, FeedbackStatus, FeedbackVote
from apps.groups.models import Group, GroupMembership, GroupMembershipStatus
from apps.product.models import IdempotencyRecord
from apps.universities.models import University


def _user(n: int, role: str = UserRole.STUDENT) -> User:
    return User.objects.create_user(
        full_name=f"Idem user {n}",
        email=f"idem{n}@example.test",
        phone_number=f"+96397700{n:04d}",
        password="StrongPass123!",
        role=role,
    )


@pytest.fixture
def world(db):
    university = University.objects.create(name="Idem University", code="IDU")
    admin = _user(1, UserRole.ADMIN)
    student = _user(2)
    StudentProfile.objects.create(
        user=student, university=university, verification_status=StudentVerificationStatus.APPROVED
    )
    group = Group.objects.create(name="Idem group", university=university, created_by=admin, requires_approval=False)
    client = APIClient()
    client.force_authenticate(student)
    return {"student": student, "group": group, "client": client, "university": university, "admin": admin}


def _post(client, url, data, key=None):
    headers = {"HTTP_IDEMPOTENCY_KEY": key} if key else {}
    return client.post(url, data, format="json", **headers)


# --- group join / leave -------------------------------------------------------------------------------------------


def test_group_join_replays_and_works_without_header(world):
    client, group, student = world["client"], world["group"], world["student"]
    url = f"/api/v1/groups/{group.pk}/join/"
    first = _post(client, url, {}, key="join-1")
    assert first.status_code == 200, first.data
    again = _post(client, url, {}, key="join-1")
    assert again.status_code == 200 and again.data == first.data
    assert GroupMembership.objects.filter(group=group, user=student).count() == 1
    # Without a key the second join is the normal business-rule rejection (not a replay).
    assert _post(client, url, {}).status_code == 400


def test_group_join_key_reuse_on_another_group_is_isolated(world):
    client, group = world["client"], world["group"]
    other = Group.objects.create(
        name="Other", university=world["university"], created_by=world["admin"], requires_approval=False
    )
    assert _post(client, f"/api/v1/groups/{group.pk}/join/", {}, key="same").status_code == 200
    assert _post(client, f"/api/v1/groups/{other.pk}/join/", {}, key="same").status_code == 200


def test_group_leave_replays(world):
    client, group, student = world["client"], world["group"], world["student"]
    GroupMembership.objects.create(group=group, user=student, status=GroupMembershipStatus.APPROVED)
    url = f"/api/v1/groups/{group.pk}/leave/"
    first = _post(client, url, {}, key="leave-1")
    assert first.status_code == 200
    again = _post(client, url, {}, key="leave-1")
    assert again.status_code == 200 and again.data == first.data


# --- feedback -----------------------------------------------------------------------------------------------------


def _feedback_body(**extra):
    return {
        "kind": FeedbackKind.SUGGESTION,
        "context": FeedbackContext.APP,
        "title": "Dark mode",
        "suggestion": "Please add a dark mode",
        **extra,
    }


def test_feedback_submit_replay_conflict_and_no_header(world):
    client, student = world["client"], world["student"]
    first = _post(client, "/api/v1/feedback/", _feedback_body(), key="fb-1")
    assert first.status_code == 201, first.data
    replay = _post(client, "/api/v1/feedback/", _feedback_body(), key="fb-1")
    assert replay.status_code == 201 and replay.data == first.data
    assert AppFeedback.objects.filter(user=student, title="Dark mode").count() == 1

    conflict = _post(client, "/api/v1/feedback/", _feedback_body(title="Other"), key="fb-1")
    assert conflict.status_code == 409
    assert "idempotency_key_reused" in str(conflict.data).lower() or "IDEMPOTENCY_KEY_REUSED" in str(conflict.data)

    no_header = _post(client, "/api/v1/feedback/", _feedback_body(title="No key"))
    assert no_header.status_code == 201


def test_feedback_validation_error_releases_the_key_so_a_corrected_retry_works(world):
    client = world["client"]
    bad = _post(client, "/api/v1/feedback/", {"kind": "nonsense"}, key="fb-fix")
    assert bad.status_code == 400
    assert not IdempotencyRecord.objects.filter(endpoint="feedback-submit").exists()
    good = _post(client, "/api/v1/feedback/", _feedback_body(), key="fb-fix")
    assert good.status_code == 201


def test_feedback_vote_toggle_is_not_undone_by_a_retry(world):
    client, student = world["client"], world["student"]
    owner = _user(3, UserRole.NORMAL_USER)
    suggestion = AppFeedback.objects.create(
        user=owner,
        kind=FeedbackKind.SUGGESTION,
        context=FeedbackContext.APP,
        title="Planned",
        suggestion="x",
        status=FeedbackStatus.PLANNED,
    )
    url = f"/api/v1/feedback/{suggestion.pk}/vote/"
    first = _post(client, url, {}, key="vote-1")
    assert first.status_code == 200 and first.data["data"]["voted"] is True
    retry = _post(client, url, {}, key="vote-1")
    assert retry.status_code == 200 and retry.data == first.data
    assert FeedbackVote.objects.filter(feedback=suggestion, user=student).count() == 1
    # A new key is a new intent: it toggles the vote off.
    assert _post(client, url, {}, key="vote-2").data["data"]["voted"] is False


# --- chat REST ----------------------------------------------------------------------------------------------------


@pytest.fixture
def chat(world):
    GroupMembership.objects.create(group=world["group"], user=world["student"], status=GroupMembershipStatus.APPROVED)
    return world


def test_chat_message_create_replay_conflict_and_no_header(chat):
    client, group = chat["client"], chat["group"]
    url = f"/api/v1/groups/{group.pk}/messages/"
    first = _post(client, url, {"content": "hello"}, key="msg-1")
    assert first.status_code == 201, first.data
    replay = _post(client, url, {"content": "hello"}, key="msg-1")
    assert replay.status_code == 201 and replay.data == first.data
    assert Message.objects.filter(group=group, content="hello").count() == 1

    conflict = _post(client, url, {"content": "different"}, key="msg-1")
    assert conflict.status_code == 409
    assert Message.objects.filter(group=group).count() == 1

    assert _post(client, url, {"content": "no key"}).status_code == 201
    assert _post(client, url, {"content": "no key"}).status_code == 201  # without a key duplicates are allowed
    assert Message.objects.filter(group=group, content="no key").count() == 2


def test_chat_message_replay_still_requires_group_access(chat):
    group = chat["group"]
    url = f"/api/v1/groups/{group.pk}/messages/"
    assert _post(chat["client"], url, {"content": "hi"}, key="k").status_code == 201
    outsider = _user(9)
    StudentProfile.objects.create(
        user=outsider, university=chat["university"], verification_status=StudentVerificationStatus.APPROVED
    )
    other = APIClient()
    other.force_authenticate(outsider)
    assert _post(other, url, {"content": "hi"}, key="k").status_code == 403


def test_chat_message_report_replay_and_conflict(chat):
    client, group, student = chat["client"], chat["group"], chat["student"]
    author = _user(4)
    StudentProfile.objects.create(
        user=author, university=chat["university"], verification_status=StudentVerificationStatus.APPROVED
    )
    GroupMembership.objects.create(group=group, user=author, status=GroupMembershipStatus.APPROVED)
    message = Message.objects.create(group=group, sender=author, content="rude")
    url = f"/api/v1/groups/{group.pk}/messages/{message.pk}/report/"
    first = _post(client, url, {"reason": "abuse"}, key="rep-1")
    assert first.status_code == 201, first.data
    replay = _post(client, url, {"reason": "abuse"}, key="rep-1")
    assert replay.status_code == 201 and replay.data == first.data
    assert MessageReport.objects.filter(message=message, reported_by=student).count() == 1
    assert _post(client, url, {"reason": "something else"}, key="rep-1").status_code == 409
    # No key: the unique (message, reporter) rule still reports the duplicate.
    assert _post(client, url, {"reason": "abuse"}).status_code == 400


# --- verification -------------------------------------------------------------------------------------------------


def test_verification_submit_rejection_releases_key(world):
    client = world["client"]
    bad = _post(client, "/api/v1/verification/submit/", {"student_number": "x"}, key="ver-1")
    assert bad.status_code == 400
    assert not IdempotencyRecord.objects.filter(endpoint="verification-submit").exists()
