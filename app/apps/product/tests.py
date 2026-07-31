from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.choices import UserRole
from apps.accounts.models import User

from .models import (
    AccountDeletionRequest,
    AccountDeletionStatus,
    DeviceInstallation,
    FeatureFlag,
    MaintenanceMode,
    MobileAppReleasePolicy,
    PrivacyPolicyVersion,
    TermsVersion,
    UserConsent,
)
from .services import AccountDeletionService


@pytest.fixture
def user(db) -> User:
    return User.objects.create_user(
        full_name="Mobile User",
        email="mobile@example.test",
        phone_number="+963955111111",
        password="StrongPass123!",
        role=UserRole.NORMAL_USER,
    )


@pytest.fixture
def client(user) -> APIClient:
    api_client = APIClient()
    api_client.force_authenticate(user)
    return api_client


def test_bootstrap_is_public_but_requires_recognized_platform(client):
    response = client.get("/api/v1/mobile/bootstrap/", HTTP_X_APP_PLATFORM="android", HTTP_X_APP_BUILD="1")
    assert response.status_code == 200
    assert response.data["code"] == "MOBILE_BOOTSTRAP"
    assert response.data["data"]["api_version"] == "v1"
    assert "feature_flags" in response.data["data"]

    invalid = client.get("/api/v1/mobile/bootstrap/", HTTP_X_APP_PLATFORM="windows")
    assert invalid.status_code == 400
    assert invalid.data["code"] == "VALIDATION_ERROR"


def test_required_update_blocks_non_exempt_mobile_api_but_not_bootstrap(client):
    MobileAppReleasePolicy.objects.create(
        platform="android",
        minimum_supported_version="2.0.0",
        minimum_supported_build=20,
        latest_version="2.1.0",
        latest_build=21,
        update_mode="required",
    )
    headers = {"HTTP_X_APP_PLATFORM": "android", "HTTP_X_APP_BUILD": "19"}
    bootstrap = client.get("/api/v1/mobile/bootstrap/", **headers)
    assert bootstrap.status_code == 200
    blocked = client.get("/api/v1/auth/me/", **headers)
    assert blocked.status_code == 426
    assert blocked.json()["code"] == "APP_UPDATE_REQUIRED"


def test_maintenance_returns_retry_after_and_exempts_health(client):
    MaintenanceMode.objects.create(enabled=True, message_en="Maintenance", retry_after_seconds=47)
    blocked = client.get("/api/v1/auth/me/")
    assert blocked.status_code == 503
    assert blocked["Retry-After"] == "47"
    health = client.get("/api/v1/health/live/")
    assert health.status_code == 200


def test_device_registration_idempotency_and_cross_account_isolation(client, user):
    installation_id = uuid4()
    payload = {
        "installation_id": str(installation_id),
        "platform": "android",
        "app_version": "1.0.0",
        "build_number": 1,
        "push_token": "ExponentPushToken[mobile-integration-test-token]",
        "locale": "ar",
    }
    first = client.post("/api/v1/mobile/devices/register/", payload, format="json", HTTP_IDEMPOTENCY_KEY="device-1")
    replay = client.post("/api/v1/mobile/devices/register/", payload, format="json", HTTP_IDEMPOTENCY_KEY="device-1")
    assert first.status_code == 201
    assert replay.status_code == 201
    assert DeviceInstallation.objects.filter(user=user).count() == 1

    changed = {**payload, "app_version": "1.0.1"}
    conflict = client.post("/api/v1/mobile/devices/register/", changed, format="json", HTTP_IDEMPOTENCY_KEY="device-1")
    assert conflict.status_code == 400

    other = User.objects.create_user(
        full_name="Other",
        email="other-mobile@example.test",
        phone_number="+963955111112",
        password="StrongPass123!",
    )
    other_client = APIClient()
    other_client.force_authenticate(other)
    forbidden = other_client.patch(f"/api/v1/mobile/devices/{installation_id}/", {"locale": "en"}, format="json")
    assert forbidden.status_code == 404


def test_policy_acceptance_records_versions_and_is_idempotent(client, user):
    TermsVersion.objects.create(version="terms-2026-01", content_url="https://example.test/terms")
    PrivacyPolicyVersion.objects.create(version="privacy-2026-01", content_url="https://example.test/privacy")
    payload = {"terms_version": "terms-2026-01", "privacy_version": "privacy-2026-01", "locale": "ar"}
    response = client.post("/api/v1/policies/accept/", payload, format="json", HTTP_IDEMPOTENCY_KEY="policy-1")
    assert response.status_code == 200
    assert UserConsent.objects.filter(user=user).count() == 2
    replay = client.post("/api/v1/policies/accept/", payload, format="json", HTTP_IDEMPOTENCY_KEY="policy-1")
    assert replay.status_code == 200
    assert UserConsent.objects.filter(user=user).count() == 2


def test_account_deletion_is_feature_gated_cancellable_and_anonymizes_when_due(client, user):
    disabled = client.post("/api/v1/account/deletion/request/", {}, format="json")
    assert disabled.status_code == 403

    FeatureFlag.objects.create(key="account_deletion_enabled", enabled=True)
    requested = client.post(
        "/api/v1/account/deletion/request/",
        {"reason": "No longer needed"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="delete-1",
    )
    assert requested.status_code == 202
    cancelled = client.post("/api/v1/account/deletion/cancel/", {}, format="json")
    assert cancelled.status_code == 200
    assert cancelled.data["data"]["status"] == AccountDeletionStatus.CANCELLED

    deletion = AccountDeletionRequest.objects.get(user=user)
    deletion.status = AccountDeletionStatus.REQUESTED
    deletion.scheduled_for = timezone.now() - timedelta(seconds=1)
    deletion.save(update_fields=["status", "scheduled_for", "updated_at"])
    assert AccountDeletionService.execute_due() == 1
    user.refresh_from_db()
    deletion.refresh_from_db()
    assert user.is_active is False
    assert user.is_deleted is True
    assert user.email.endswith("@invalid.local")
    assert deletion.status == AccountDeletionStatus.COMPLETED


def test_feature_scope_prefers_platform_and_role(client, user):
    FeatureFlag.objects.create(key="feedback_enabled", enabled=False)
    FeatureFlag.objects.create(key="feedback_enabled", enabled=True, platform="android", role=user.role)
    bootstrap = client.get("/api/v1/mobile/bootstrap/", HTTP_X_APP_PLATFORM="android")
    assert bootstrap.status_code == 200
    assert bootstrap.data["data"]["feature_flags"]["feedback_enabled"] is False
