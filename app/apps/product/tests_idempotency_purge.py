from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from django.db.models import QuerySet
from django.test import override_settings
from django.utils import timezone

from apps.product.models import IdempotencyRecord
from apps.product.services import IdempotencyService
from apps.product.tasks import purge_expired_idempotency_records


def fake_request(user=1, key="k-1", data=None, meta=None):
    return SimpleNamespace(
        headers={"Idempotency-Key": key},
        user=SimpleNamespace(pk=user) if user else SimpleNamespace(pk=None),
        META=meta or {},
        data=data or {"a": 1},
    )


def _make_record(**overrides):
    now = timezone.now()
    defaults = {
        "actor_key": "user:1",
        "endpoint": "e",
        "key_hash": "h" * 64,
        "payload_hash": "p" * 64,
        "expires_at": now + timedelta(hours=1),
    }
    return IdempotencyRecord.objects.create(**(defaults | overrides))


@pytest.mark.django_db
def test_begin_no_longer_deletes_expired_rows_on_the_hot_path():
    stale = _make_record(key_hash="a" * 64, expires_at=timezone.now() - timedelta(hours=1))
    IdempotencyService.begin(fake_request(key="fresh"), endpoint="x")
    assert IdempotencyRecord.objects.filter(pk=stale.pk).exists()


@pytest.mark.django_db
def test_expired_row_for_the_same_key_is_recycled_not_replayed():
    first = IdempotencyService.begin(fake_request(), endpoint="x")
    assert first.record_id is not None
    IdempotencyService.complete(first, SimpleNamespace(status_code=201, data={"old": True}))
    IdempotencyRecord.objects.filter(pk=first.record_id).update(expires_at=timezone.now() - timedelta(minutes=1))
    again = IdempotencyService.begin(fake_request(data={"different": 2}), endpoint="x")
    assert again.record_id == first.record_id
    assert again.replay_body is None
    record = IdempotencyRecord.objects.get(pk=first.record_id)
    assert record.response_status is None and record.expires_at > timezone.now()


@pytest.mark.django_db
def test_purge_task_deletes_only_expired_rows_in_batches():
    now = timezone.now()
    for i in range(7):
        _make_record(key_hash=f"{i:064d}", expires_at=now - timedelta(minutes=5))
    keep = _make_record(key_hash="f" * 64)
    with mock.patch.object(QuerySet, "delete", autospec=True, side_effect=QuerySet.delete) as delete:
        removed = purge_expired_idempotency_records(batch_size=3)
    assert removed == 7
    assert delete.call_count == 3  # 3 + 3 + 1
    assert list(IdempotencyRecord.objects.values_list("pk", flat=True)) == [keep.pk]
    assert purge_expired_idempotency_records() == 0


def test_purge_task_is_scheduled_daily():
    from django.conf import settings

    entry = settings.CELERY_BEAT_SCHEDULE["purge-expired-idempotency-records"]
    assert entry["task"] == "apps.product.tasks.purge_expired_idempotency_records"
    assert "crontab" in repr(entry["schedule"]).lower()


@pytest.mark.django_db
@override_settings(TRUSTED_PROXY_COUNT=1)
def test_anonymous_actor_key_uses_trusted_client_ip_not_remote_addr():
    meta = {"REMOTE_ADDR": "10.0.0.9", "HTTP_X_FORWARDED_FOR": "198.51.100.7"}
    decision = IdempotencyService.begin(fake_request(user=None, meta=meta), endpoint="x")
    assert decision.record_id is not None
    assert IdempotencyRecord.objects.get(pk=decision.record_id).actor_key == "ip:198.51.100.7"


class _RaceyManager:
    """Simulates a concurrent request winning the takeover between our read and our compare-and-set."""

    def __init__(self, real):
        self.real = real

    def get(self, **kwargs):
        record = self.real.get(**kwargs)
        IdempotencyRecord.objects.filter(pk=record.pk).update(updated_at=timezone.now())
        return record


@pytest.mark.django_db
def test_in_progress_takeover_is_compare_and_set_and_loses_the_race():
    first = IdempotencyService.begin(fake_request(), endpoint="x")
    assert first.record_id is not None
    IdempotencyRecord.objects.filter(pk=first.record_id).update(
        updated_at=timezone.now() - timedelta(seconds=IdempotencyService.IN_PROGRESS_TIMEOUT_SECONDS + 5)
    )
    real_manager = IdempotencyRecord.objects
    real_select = real_manager.select_for_update
    with mock.patch.object(type(real_manager), "select_for_update", lambda self: _RaceyManager(real_select())):
        with pytest.raises(RuntimeError, match="already in progress"):
            IdempotencyService.begin(fake_request(), endpoint="x")


@pytest.mark.django_db
def test_in_progress_takeover_still_succeeds_without_a_race():
    first = IdempotencyService.begin(fake_request(), endpoint="x")
    assert first.record_id is not None
    IdempotencyRecord.objects.filter(pk=first.record_id).update(
        updated_at=timezone.now() - timedelta(seconds=IdempotencyService.IN_PROGRESS_TIMEOUT_SECONDS + 5)
    )
    assert IdempotencyService.begin(fake_request(), endpoint="x").record_id == first.record_id
    # The winner refreshed updated_at, so an immediate third attempt is blocked again.
    with pytest.raises(RuntimeError):
        IdempotencyService.begin(fake_request(), endpoint="x")
