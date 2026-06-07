import logging

import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.common.logging import RequestIDLogFilter, reset_request_id, sanitize_for_logging, set_request_id


@pytest.mark.django_db
def test_request_id_header_is_generated_for_public_health_endpoint():
    response = APIClient().get("/api/v1/health/")

    assert response.status_code == 200
    assert response["X-Request-ID"]
    assert response.data["success"] is True


@pytest.mark.django_db
def test_valid_request_id_header_is_preserved():
    response = APIClient().get("/api/v1/health/", HTTP_X_REQUEST_ID="phase4-test-123")

    assert response.status_code == 200
    assert response["X-Request-ID"] == "phase4-test-123"


@pytest.mark.django_db
def test_invalid_request_id_header_is_replaced():
    response = APIClient().get("/api/v1/health/", HTTP_X_REQUEST_ID="bad value with spaces")

    assert response.status_code == 200
    assert response["X-Request-ID"] != "bad value with spaces"
    assert response["X-Request-ID"]


def test_sanitize_for_logging_redacts_nested_sensitive_values():
    payload = {
        "email": "student@example.com",
        "password": "secret",
        "profile": {
            "otp": "123456",
            "items": [{"token": "abc"}, {"name": "safe"}],
        },
    }

    sanitized = sanitize_for_logging(payload)

    assert sanitized["email"] == "student@example.com"
    assert sanitized["password"] == "[REDACTED]"
    assert sanitized["profile"]["otp"] == "[REDACTED]"
    assert sanitized["profile"]["items"][0]["token"] == "[REDACTED]"
    assert sanitized["profile"]["items"][1]["name"] == "safe"


def test_request_id_log_filter_adds_current_request_id():
    token = set_request_id("phase4-log-id")
    try:
        record = logging.getLogger("phase4").makeRecord(
            name="phase4",
            level=logging.INFO,
            fn=__file__,
            lno=1,
            msg="test",
            args=(),
            exc_info=None,
        )

        assert RequestIDLogFilter().filter(record) is True
        assert record.request_id == "phase4-log-id"
    finally:
        reset_request_id(token)


def test_openapi_schema_generation_validates(tmp_path):
    schema_path = tmp_path / "openapi.yml"

    call_command("spectacular", file=str(schema_path), validate=True, verbosity=0)

    assert schema_path.exists()
    assert schema_path.read_text(encoding="utf-8").startswith("openapi:")
