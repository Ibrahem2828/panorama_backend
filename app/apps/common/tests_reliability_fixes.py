"""Regression tests for silent failures: log redaction, audit serialisation and PII redaction."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from apps.audit.services import sanitize_value
from apps.common.logging import JSONFormatter, SensitiveDataFilter
from apps.feedback.services import FeedbackService, abuse_flags
from apps.feedback.tasks import redact_feedback_text


def make_record(msg, *args, exc_info=None):
    return logging.LogRecord("test", logging.INFO, __file__, 1, msg, args, exc_info)


def test_log_filter_redacts_values_and_keeps_the_key_name():
    record = make_record("login failed token=abc123 for user")
    SensitiveDataFilter().filter(record)
    assert "abc123" not in record.getMessage()
    assert "token=[REDACTED]" in record.getMessage()


def test_log_filter_keeps_percent_arguments_and_redacts_inside_them():
    record = make_record("user %s sent %s", 42, "password: hunter2")
    SensitiveDataFilter().filter(record)
    message = record.getMessage()
    assert "42" in message
    assert "hunter2" not in message


def test_json_formatter_keeps_call_sites_but_not_exception_text():
    try:
        raise ValueError("secret user input")
    except ValueError:
        import sys

        record = make_record("boom", exc_info=sys.exc_info())
    payload = json.loads(JSONFormatter().format(record))
    assert payload["exception"] == "ValueError"
    assert any("test_json_formatter" in frame for frame in payload["frames"])
    assert "secret user input" not in json.dumps(payload)


def test_audit_sanitizer_makes_decimals_dates_and_uuids_storable():
    value = {
        "price": Decimal("12.50"),
        "when": datetime(2026, 1, 2, 3, 4, tzinfo=UTC),
        "id": uuid4(),
        "nested": [{"amount": Decimal("1.00")}],
        "email": "a@b.test",
    }
    clean = sanitize_value(value)
    json.dumps(clean)  # must not raise: this is what the JSONField does on save
    assert clean["price"] == "12.50"
    assert clean["email"] == "[REDACTED]"
    assert clean["nested"][0]["amount"] == "1.00"


def test_feedback_redaction_actually_removes_pii():
    text = "mail me at ali@example.com or +963 944 123 456, token: abc123, see https://evil.test/x"
    redacted = redact_feedback_text(text)
    assert "ali@example.com" not in redacted
    assert "944 123 456" not in redacted
    assert "abc123" not in redacted
    assert "evil.test" not in redacted


def test_feedback_abuse_flag_detects_repeated_characters():
    assert "repeated_characters" in abuse_flags({"comment": "a" * 30})
    assert "repeated_characters" not in abuse_flags({"comment": "a normal sentence"})


@pytest.mark.django_db
def test_feedback_analytics_does_not_fall_back_to_everything_for_an_empty_queryset():
    from unittest import mock

    from apps.feedback.models import AppFeedback

    empty = AppFeedback.objects.none()
    with mock.patch("apps.feedback.services.AppFeedback") as model:
        FeedbackService.analytics(empty)
    model.objects.filter.assert_not_called()
