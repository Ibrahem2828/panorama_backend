from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from django.contrib import admin
from django.db import models
from django.test import override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.admin import AuditLogAdmin
from apps.audit.models import AuditAction, AuditLog, AuditLogImmutableError
from apps.audit.tasks import purge_expired_audit_logs

pytestmark = pytest.mark.django_db


def _row(action=AuditAction.FILE_UPDATED, age_days=0, **extra) -> AuditLog:
    row = AuditLog.objects.create(action=action, **extra)
    if age_days:
        # Back-dating is itself a mutation, so the test goes through the base queryset on purpose.
        models.QuerySet.update(AuditLog.objects.filter(pk=row.pk), created_at=timezone.now() - timedelta(days=age_days))
    return row


def test_existing_row_cannot_be_saved_or_deleted():
    row = _row()
    row.target_type = "tampered"
    with pytest.raises(AuditLogImmutableError):
        row.save()
    with pytest.raises(AuditLogImmutableError):
        row.save(update_fields=["target_type"])
    with pytest.raises(AuditLogImmutableError):
        row.delete()
    assert AuditLog.objects.get(pk=row.pk).target_type == ""


def test_queryset_and_manager_bulk_mutations_are_blocked():
    row = _row()
    with pytest.raises(AuditLogImmutableError):
        AuditLog.objects.filter(pk=row.pk).update(target_type="x")
    with pytest.raises(AuditLogImmutableError):
        AuditLog.objects.update(target_type="x")
    with pytest.raises(AuditLogImmutableError):
        AuditLog.objects.filter(pk=row.pk).delete()
    with pytest.raises(AuditLogImmutableError):
        AuditLog.objects.all().delete()
    with pytest.raises(AuditLogImmutableError):
        AuditLog.objects.bulk_update([row], ["target_type"])
    with pytest.raises(AuditLogImmutableError):
        AuditLog.objects.update_or_create(pk=row.pk, defaults={"target_type": "x"})
    assert AuditLog.objects.filter(pk=row.pk, target_type="").count() == 1


def test_appending_rows_still_works():
    AuditLog.objects.create(action=AuditAction.FILE_UPDATED)
    AuditLog.objects.bulk_create([AuditLog(action=AuditAction.FILE_DELETED), AuditLog(action=AuditAction.FILE_DELETED)])
    assert AuditLog.objects.count() == 3


def test_admin_is_read_only(rf):
    superuser = User.objects.create_superuser(
        full_name="Root", email="root@example.test", phone_number="+963955000009", password="StrongPass123!"
    )
    model_admin = AuditLogAdmin(AuditLog, admin.site)
    request = rf.get("/")
    request.user = superuser
    row = _row()
    assert model_admin.has_add_permission(request) is False
    assert model_admin.has_change_permission(request, row) is False
    assert model_admin.has_delete_permission(request, row) is False
    assert set(model_admin.get_readonly_fields(request, row)) >= {"action", "old_value", "new_value", "actor"}


def test_scrub_network_identifiers_for_actor_is_narrow_and_works():
    actor = User.objects.create_user(
        full_name="A", email="a@example.test", phone_number="+963955000001", password="StrongPass123!"
    )
    other = User.objects.create_user(
        full_name="B", email="b@example.test", phone_number="+963955000002", password="StrongPass123!"
    )
    mine = _row(actor=actor, ip_address="10.0.0.1", user_agent="UA", target_type="keep")
    theirs = _row(actor=other, ip_address="10.0.0.2", user_agent="UA2")
    assert AuditLog.objects.scrub_network_identifiers_for(actor) == 1
    mine.refresh_from_db()
    theirs.refresh_from_db()
    assert mine.ip_address is None and mine.user_agent == "" and mine.target_type == "keep"
    assert theirs.ip_address == "10.0.0.2" and theirs.user_agent == "UA2"


def test_retention_method_deletes_only_rows_older_than_cutoff():
    old = _row(age_days=10)
    new = _row(age_days=1)
    deleted = AuditLog.objects.purge_older_than(timezone.now() - timedelta(days=5))
    assert deleted == 1
    assert not AuditLog.objects.filter(pk=old.pk).exists()
    assert AuditLog.objects.filter(pk=new.pk).exists()
    with pytest.raises(ValueError):
        AuditLog.objects.purge_older_than(timezone.now() + timedelta(days=1))


@override_settings(AUDIT_LOG_RETENTION_DAYS=30)
def test_purge_task_batches_keeps_recent_rows_and_logs_one_summary(caplog):
    old = [_row(age_days=31) for _ in range(5)]
    fresh = [_row(age_days=29), _row()]
    with caplog.at_level(logging.INFO, logger="apps.audit.tasks"):
        deleted = purge_expired_audit_logs(batch_size=2)
    assert deleted == len(old)
    assert AuditLog.objects.filter(pk__in=[row.pk for row in fresh]).count() == 2
    assert AuditLog.objects.count() == 2  # the purge itself wrote no audit rows
    summaries = [record for record in caplog.records if record.getMessage() == "audit_retention_purge_completed"]
    assert len(summaries) == 1
    assert summaries[0].deleted == 5 and summaries[0].batches == 3


@pytest.mark.parametrize("days", [0, -1])
def test_purge_task_keeps_everything_when_retention_is_zero_or_negative(days):
    _row(age_days=5000)
    with override_settings(AUDIT_LOG_RETENTION_DAYS=days):
        assert purge_expired_audit_logs() == 0
    assert AuditLog.objects.count() == 1


def test_purge_task_is_scheduled_daily():
    from celery.schedules import crontab
    from django.conf import settings

    entry = settings.CELERY_BEAT_SCHEDULE["purge-expired-audit-logs"]
    assert entry["task"] == "apps.audit.tasks.purge_expired_audit_logs"
    assert isinstance(entry["schedule"], crontab)
