"""Optional policy acceptance sent with registration: recorded when valid, rejected when stale, inert when absent."""

from __future__ import annotations

import pytest
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.audit.models import AuditAction, AuditLog
from apps.product.models import PrivacyPolicyVersion, TermsVersion, UserConsent

NORMAL_URL = "/api/v1/auth/register/normal/"
STUDENT_URL = "/api/v1/auth/register/student/"


def _payload(n: int, **extra) -> dict:
    return {
        "full_name": f"Consent User {n}",
        "email": f"consent{n}@example.test",
        "phone_number": f"+96395511{n:04d}",
        "password": "StrongPass123!",
        "password_confirm": "StrongPass123!",
        **extra,
    }


@pytest.fixture(autouse=True)
def _policies(db):
    cache.clear()
    TermsVersion.objects.create(version="terms-2026-02")
    TermsVersion.objects.create(version="terms-old", is_active=False)
    PrivacyPolicyVersion.objects.create(version="privacy-2026-02")


@pytest.mark.django_db
@pytest.mark.parametrize("url", [NORMAL_URL, STUDENT_URL])
def test_matching_versions_are_recorded_with_the_registration(url):
    response = APIClient().post(
        url,
        _payload(1, accept_terms_version="terms-2026-02", accept_privacy_version="privacy-2026-02"),
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED
    user = User.objects.get(email="consent1@example.test")
    consents = {(c.kind, c.version) for c in UserConsent.objects.filter(user=user)}
    assert consents == {("terms", "terms-2026-02"), ("privacy", "privacy-2026-02")}
    entry = AuditLog.objects.get(action=AuditAction.POLICY_ACCEPTED, actor=user)
    assert entry.new_value == {"kinds": ["terms", "privacy"], "source": "registration"}
    assert "accept_terms_version" not in response.data["data"]["user"]


@pytest.mark.django_db
@pytest.mark.parametrize("url", [NORMAL_URL, STUDENT_URL])
def test_absent_or_blank_fields_change_nothing(url):
    client = APIClient()
    assert client.post(url, _payload(2), format="json").status_code == status.HTTP_201_CREATED
    blank = client.post(url, _payload(3, accept_terms_version="", accept_privacy_version=""), format="json")
    assert blank.status_code == status.HTTP_201_CREATED
    assert UserConsent.objects.count() == 0
    assert not AuditLog.objects.filter(action=AuditAction.POLICY_ACCEPTED).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("url", [NORMAL_URL, STUDENT_URL])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("accept_terms_version", "terms-unknown"),
        ("accept_terms_version", "terms-old"),  # exists but is no longer active
        ("accept_privacy_version", "terms-2026-02"),  # a terms version is not a privacy version
    ],
)
def test_mismatching_version_is_a_field_error_and_nothing_is_created(url, field, value):
    response = APIClient().post(url, _payload(4, **{field: value}), format="json")
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert field in response.data["errors"]
    assert not User.objects.filter(email="consent4@example.test").exists()
    assert UserConsent.objects.count() == 0


@pytest.mark.django_db
def test_only_one_of_the_two_versions_can_be_sent():
    response = APIClient().post(NORMAL_URL, _payload(5, accept_privacy_version="privacy-2026-02"), format="json")
    assert response.status_code == status.HTTP_201_CREATED
    assert list(UserConsent.objects.values_list("kind", "version")) == [("privacy", "privacy-2026-02")]


@pytest.mark.django_db
def test_a_failed_registration_leaves_no_consent_behind():
    first = APIClient().post(NORMAL_URL, _payload(6), format="json")
    assert first.status_code == status.HTTP_201_CREATED
    duplicate = APIClient().post(NORMAL_URL, _payload(6, accept_terms_version="terms-2026-02"), format="json")
    assert duplicate.status_code == status.HTTP_400_BAD_REQUEST
    assert UserConsent.objects.count() == 0


@pytest.mark.django_db
def test_policy_status_reflects_the_registration_acceptance():
    client = APIClient()
    client.post(NORMAL_URL, _payload(7, accept_terms_version="terms-2026-02"), format="json")
    user = User.objects.get(email="consent7@example.test")
    client.force_authenticate(user)
    status_response = client.get("/api/v1/policies/status/")
    assert status_response.status_code == status.HTTP_200_OK
    assert status_response.data["data"]["terms"]["accepted"] is True
    assert status_response.data["data"]["privacy"]["accepted"] is False
