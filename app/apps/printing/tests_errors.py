import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.common.tests_phase3 import PNG_BYTES, configure_printing

ITEM = {"copies": 1, "color_mode": "black_white", "paper_size": "A4", "sides": "one_sided", "binding": "none"}


@pytest.fixture
def client(db):
    user = User.objects.create_user(
        full_name="Normal",
        email="errors@example.com",
        phone_number="+963944440003",
        password="StrongPass123!",
        role=UserRole.NORMAL_USER,
    )
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def quote(client, **extra):
    upload = SimpleUploadedFile("a.png", PNG_BYTES, content_type="image/png")
    return client.post(
        "/api/v1/printing/quote/",
        {"items[0]uploaded_file": upload, **{f"items[0]{k}": v for k, v in {**ITEM, **extra}.items()}},
        format="multipart",
    )


@pytest.mark.django_db
def test_quote_without_a_configured_price_reports_a_stable_machine_code(client):
    response = quote(client)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["code"] == "PRINT_PRICING_UNAVAILABLE"


@pytest.mark.django_db
def test_quote_with_unavailable_binding_reports_a_stable_machine_code(client):
    configure_printing()
    response = quote(client, binding="spiral")
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["code"] == "PRINT_BINDING_UNAVAILABLE"


@pytest.mark.django_db
def test_quote_succeeds_once_a_price_exists(client):
    configure_printing()
    assert quote(client).status_code == status.HTTP_200_OK
