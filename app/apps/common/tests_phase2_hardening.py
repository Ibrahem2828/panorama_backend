from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.audit.models import AuditAction, AuditLog
from apps.audit.services import AuditLogService, audit_failure_key, audit_failures_today
from apps.common import tasks as common_tasks
from apps.common.request_utils import get_client_ip
from apps.common.throttles import DefaultAnonRateThrottle, DefaultUserRateThrottle

SECRET = "bff-test-secret-value"


def _request(**meta):
    return SimpleNamespace(META=meta)


# ---------------------------------------------------------------- item 6: BFF trusted client IP


@override_settings(BFF_SHARED_SECRET=SECRET, TRUSTED_PROXY_COUNT=1)
def test_valid_bff_secret_and_ip_are_honoured():
    request = _request(
        REMOTE_ADDR="10.0.0.5",
        HTTP_X_FORWARDED_FOR="10.0.0.9",
        HTTP_X_PANORAMA_BFF_SECRET=SECRET,
        HTTP_X_PANORAMA_CLIENT_IP="  203.0.113.7 ",
    )
    assert get_client_ip(request) == "203.0.113.7"
    v6 = _request(REMOTE_ADDR="10.0.0.5", HTTP_X_PANORAMA_BFF_SECRET=SECRET, HTTP_X_PANORAMA_CLIENT_IP="2001:db8::1")
    assert get_client_ip(v6) == "2001:db8::1"


@override_settings(BFF_SHARED_SECRET=SECRET, TRUSTED_PROXY_COUNT=1)
def test_wrong_or_missing_secret_is_ignored():
    base = {"REMOTE_ADDR": "10.0.0.5", "HTTP_X_FORWARDED_FOR": "198.51.100.1"}
    wrong = _request(**base, HTTP_X_PANORAMA_BFF_SECRET="nope", HTTP_X_PANORAMA_CLIENT_IP="203.0.113.7")
    missing = _request(**base, HTTP_X_PANORAMA_CLIENT_IP="203.0.113.7")
    assert get_client_ip(wrong) == "198.51.100.1"
    assert get_client_ip(missing) == "198.51.100.1"


@override_settings(BFF_SHARED_SECRET=SECRET, TRUSTED_PROXY_COUNT=1)
@pytest.mark.parametrize("bad", ["", "not-an-ip", "203.0.113.7, 1.1.1.1", "999.1.1.1", "1.1.1.1:80", "<script>"])
def test_invalid_client_ip_is_ignored(bad):
    request = _request(
        REMOTE_ADDR="10.0.0.5",
        HTTP_X_FORWARDED_FOR="198.51.100.1",
        HTTP_X_PANORAMA_BFF_SECRET=SECRET,
        HTTP_X_PANORAMA_CLIENT_IP=bad,
    )
    assert get_client_ip(request) == "198.51.100.1"


@override_settings(BFF_SHARED_SECRET="", TRUSTED_PROXY_COUNT=1)
def test_feature_off_ignores_the_header_even_with_an_empty_secret():
    request = _request(
        REMOTE_ADDR="10.0.0.5",
        HTTP_X_FORWARDED_FOR="198.51.100.1",
        HTTP_X_PANORAMA_BFF_SECRET="",
        HTTP_X_PANORAMA_CLIENT_IP="203.0.113.7",
    )
    assert get_client_ip(request) == "198.51.100.1"


@override_settings(BFF_SHARED_SECRET=SECRET, TRUSTED_PROXY_COUNT=1)
def test_xff_spoofing_is_still_governed_by_trusted_proxy_count():
    spoofed = _request(REMOTE_ADDR="10.0.0.5", HTTP_X_FORWARDED_FOR="6.6.6.6, 198.51.100.1")
    assert get_client_ip(spoofed) == "198.51.100.1"  # the attacker-controlled left entry is ignored
    with override_settings(TRUSTED_PROXY_COUNT=0):
        assert get_client_ip(spoofed) == "10.0.0.5"


def test_bff_headers_are_not_allowed_from_browsers_and_secret_is_not_in_settings_dump():
    from corsheaders.defaults import default_headers
    from django.conf import settings

    allowed = {h.lower() for h in getattr(settings, "CORS_ALLOW_HEADERS", default_headers)}
    assert "x-panorama-bff-secret" not in allowed
    assert "x-panorama-client-ip" not in allowed
    exposed = {h.lower() for h in getattr(settings, "CORS_EXPOSE_HEADERS", [])}
    assert not {"x-panorama-bff-secret", "x-panorama-client-ip"} & exposed


@pytest.mark.django_db
@override_settings(BFF_SHARED_SECRET=SECRET, TRUSTED_PROXY_COUNT=1)
def test_bff_ip_reaches_the_response_pipeline_without_leaking_the_secret():
    client = APIClient()
    response = client.get(
        "/api/v1/health/live/",
        REMOTE_ADDR="10.0.0.5",
        HTTP_X_PANORAMA_BFF_SECRET=SECRET,
        HTTP_X_PANORAMA_CLIENT_IP="203.0.113.7",
    )
    assert response.status_code == 200
    assert SECRET not in response.content.decode()
    assert SECRET not in str(dict(response.headers))


# ---------------------------------------------------------------- item 7: token housekeeping


def test_flush_expired_jwt_tokens_task_calls_the_management_command():
    with patch.object(common_tasks, "call_command") as call_command:
        common_tasks.flush_expired_jwt_tokens()
    call_command.assert_called_once_with("flushexpiredtokens")


def test_token_flush_is_scheduled_daily():
    from django.conf import settings

    entry = settings.CELERY_BEAT_SCHEDULE["flush-expired-jwt-tokens"]
    assert entry["task"] == "apps.common.tasks.flush_expired_jwt_tokens"
    assert "crontab" in repr(entry["schedule"]).lower()


@pytest.mark.django_db
def test_flushexpiredtokens_command_actually_runs():
    from django.core.management import call_command

    call_command("flushexpiredtokens")


# ---------------------------------------------------------------- item 5: default throttles


def test_default_throttles_are_configured_with_generous_rates():
    from django.conf import settings

    classes = cast("list[str]", settings.REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"])
    assert "apps.common.throttles.DefaultUserRateThrottle" in classes
    assert "apps.common.throttles.DefaultAnonRateThrottle" in classes
    rates = cast("dict[str, str]", settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"])
    assert rates["user"] == "600/min" and rates["anon"] == "120/min"


@pytest.fixture
def staff_user(db):
    return User.objects.create_user(
        full_name="Throttle User",
        email="throttle@example.com",
        phone_number="+963900555111",
        password="StrongTestPassword123!",
        role=UserRole.NORMAL_USER,
    )


@pytest.mark.django_db
def test_unthrottled_list_endpoint_returns_429_after_the_default_limit(staff_user):
    client = APIClient()
    client.force_authenticate(staff_user)
    tiny = {"user": "3/min", "anon": "2/min"}
    with patch.object(DefaultUserRateThrottle, "THROTTLE_RATES", tiny):
        statuses = [client.get("/api/v1/notifications/").status_code for _ in range(5)]
    assert statuses[:3] == [200, 200, 200]
    assert statuses[3] == 429 and statuses[4] == 429


@pytest.mark.django_db
def test_anonymous_default_throttle_uses_trusted_client_ip():
    tiny = {"user": "100/min", "anon": "2/min"}
    client = APIClient()
    with patch.object(DefaultAnonRateThrottle, "THROTTLE_RATES", tiny):
        first = [
            client.get("/api/v1/universities/", REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR="198.51.100.1").status_code
            for _ in range(3)
        ]
        other_client = client.get(
            "/api/v1/universities/", REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR="198.51.100.2"
        ).status_code
    assert first[-1] == 429
    assert other_client != 429  # a different real client behind the same proxy has its own bucket


@pytest.mark.django_db
def test_health_probes_are_exempt_from_default_throttles():
    tiny = {"user": "1/min", "anon": "1/min"}
    client = APIClient()
    with patch.object(DefaultAnonRateThrottle, "THROTTLE_RATES", tiny):
        assert [client.get("/api/v1/health/live/").status_code for _ in range(4)] == [200] * 4


def test_per_view_throttle_classes_override_the_defaults():
    from apps.accounts.views import LoginView  # noqa: F401  (import proves the view still declares its own)

    assert LoginView.throttle_classes
    assert DefaultAnonRateThrottle not in LoginView.throttle_classes


# ---------------------------------------------------------------- item 4: audit log best effort


def _failing_create(*args, **kwargs):
    raise RuntimeError("db down")


@pytest.mark.django_db
def test_audit_failure_is_swallowed_logged_at_error_and_counted(caplog):
    with patch.object(AuditLog.objects, "create", side_effect=_failing_create):
        with caplog.at_level(logging.ERROR, logger="apps.audit.services"):
            assert AuditLogService.log(action=AuditAction.FILE_UPDATED) is None
            assert AuditLogService.log(action=AuditAction.FILE_UPDATED) is None
    records = [r for r in caplog.records if r.getMessage() == "audit_log_write_failed"]
    assert len(records) == 2
    assert records[0].levelno == logging.ERROR
    assert records[0].audit_action == AuditAction.FILE_UPDATED
    assert records[0].failure_class == "RuntimeError"
    assert records[0].audit_critical is False
    assert cache.get(audit_failure_key()) == 2
    assert audit_failures_today() == 2


@pytest.mark.django_db
def test_critical_audit_failure_reraises_but_is_still_counted():
    with patch.object(AuditLog.objects, "create", side_effect=_failing_create):
        with pytest.raises(RuntimeError, match="db down"):
            AuditLogService.log(action=AuditAction.ACCOUNT_DELETION_COMPLETED, critical=True)
    assert audit_failures_today() == 1


@pytest.mark.django_db
def test_successful_audit_write_does_not_touch_the_counter():
    row = AuditLogService.log(action=AuditAction.FILE_UPDATED, critical=True)
    assert row is not None and audit_failures_today() == 0
    assert cache.get(audit_failure_key()) is None


@pytest.mark.django_db
def test_counter_failure_never_breaks_the_best_effort_path():
    with patch.object(AuditLog.objects, "create", side_effect=_failing_create):
        with patch("apps.audit.services.cache.add", side_effect=RuntimeError("cache down")):
            assert AuditLogService.log(action=AuditAction.FILE_UPDATED) is None


@pytest.mark.django_db
def test_readiness_payload_exposes_the_audit_failure_counter():
    cache.set(audit_failure_key(timezone.now().date()), 4, 60)
    response = APIClient().get("/api/v1/health/ready/")
    body = response.json()
    payload = body.get("data", body)
    assert response.status_code == 200
    assert payload["audit_failures_today"] == 4


@pytest.mark.django_db
def test_account_deletion_rolls_back_when_the_critical_audit_write_fails():
    from datetime import timedelta

    from apps.product.models import AccountDeletionRequest, AccountDeletionStatus
    from apps.product.services import AccountDeletionService

    user = User.objects.create_user(
        full_name="Doomed",
        email="doomed@example.com",
        phone_number="+963900777222",
        password="StrongTestPassword123!",
        role=UserRole.NORMAL_USER,
    )
    AccountDeletionService.request(user)
    AccountDeletionRequest.objects.filter(user=user).update(scheduled_for=timezone.now() - timedelta(days=1))
    with patch.object(AuditLog.objects, "create", side_effect=_failing_create):
        with pytest.raises(RuntimeError):
            AccountDeletionService.execute_due()
    deletion = AccountDeletionRequest.objects.get(user=user)
    user.refresh_from_db()
    assert deletion.status == AccountDeletionStatus.REQUESTED
    assert user.is_deleted is False
