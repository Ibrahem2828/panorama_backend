from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from django.utils import timezone

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.notifications.models import NotificationPreference
from apps.notifications.services import NotificationService
from apps.product.models import IdempotencyRecord
from apps.product.services import IdempotencyService


def fake_request(user_pk=1, key="k-1", data=None):
    return SimpleNamespace(
        headers={"Idempotency-Key": key}, user=SimpleNamespace(pk=user_pk), META={}, data=data or {"a": 1}
    )


@pytest.mark.django_db
def test_a_fresh_unfinished_attempt_still_blocks_a_concurrent_retry():
    IdempotencyService.begin(fake_request(), endpoint="x")
    with pytest.raises(RuntimeError):
        IdempotencyService.begin(fake_request(), endpoint="x")


@pytest.mark.django_db
def test_a_stuck_attempt_can_be_taken_over_instead_of_409_for_a_day():
    first = IdempotencyService.begin(fake_request(), endpoint="x")
    IdempotencyRecord.objects.filter(pk=first.record_id).update(
        updated_at=timezone.now() - timedelta(seconds=IdempotencyService.IN_PROGRESS_TIMEOUT_SECONDS + 5)
    )
    retry = IdempotencyService.begin(fake_request(), endpoint="x")
    assert retry.record_id == first.record_id
    assert retry.replay_body is None


@pytest.mark.django_db
def test_a_completed_attempt_replays_its_response():
    first = IdempotencyService.begin(fake_request(), endpoint="x")
    IdempotencyService.complete(first, SimpleNamespace(status_code=201, data={"ok": True}))
    replay = IdempotencyService.begin(fake_request(), endpoint="x")
    assert (replay.replay_status, replay.replay_body) == (201, {"ok": True})


def make_user(n):
    return User.objects.create_user(
        full_name=f"U{n}",
        email=f"u{n}@example.test",
        phone_number=f"+96398800{n:04d}",
        password="StrongPass123!",
        role=UserRole.NORMAL_USER,
    )


@pytest.mark.django_db
def test_bulk_notifications_do_not_push_to_users_who_disabled_push(settings, django_capture_on_commit_callbacks):
    settings.PUSH_NOTIFICATIONS_ENABLED = True
    on, off = make_user(1), make_user(2)
    NotificationPreference.objects.create(user=off, push_enabled=False)
    with mock.patch("apps.notifications.tasks.deliver_push_notification.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            created = NotificationService.create_bulk_notifications([on, off], "t", "b")
    assert len(created) == 2  # the in-app notification is still created for both
    pushed_to = {call.args[0] for call in delay.call_args_list}
    assert pushed_to == {on.id}
