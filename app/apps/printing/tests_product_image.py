"""Ready-product images are served through ``/api/v1/printing/products/{id}/image/`` (``/media/`` is not published)."""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO

import pytest
from django.core.files.base import ContentFile
from PIL import Image
from rest_framework.test import APIClient

from apps.accounts.choices import UserRole
from apps.accounts.models import User

from .models import ReadyProduct


def _user(role, n):
    return User.objects.create_user(
        full_name=f"Prod {role}",
        email=f"prodimg-{n}@example.test",
        phone_number=f"+96394447{n:04d}",
        password="StrongPass123!",
        role=role,
    )


def _client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def _png_bytes():
    buffer = BytesIO()
    Image.new("RGB", (10, 10), "red").save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)


@pytest.fixture
def staff(db):
    return _client(_user(UserRole.PRINT_STAFF, 1))


@pytest.fixture
def student(db):
    return _client(_user(UserRole.STUDENT, 2))


def _product(*, image=True, **extra):
    product = ReadyProduct.objects.create(name="notebook", price=Decimal("5500"), **extra)
    if image:
        product.image.save("p.png", ContentFile(_png_bytes()))
    return product


def test_requires_authentication(db):
    product = _product()
    assert APIClient().get(f"/api/v1/printing/products/{product.pk}/image/").status_code in (401, 403)


def test_served_inline_with_headers_etag_and_304(student):
    product = _product()
    url = f"/api/v1/printing/products/{product.pk}/image/"
    response = student.get(url)
    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Cache-Control"].startswith("private")
    assert b"".join(response.streaming_content) == _png_bytes()
    again = student.get(url, HTTP_IF_NONE_MATCH=response["ETag"])
    assert again.status_code == 304


def test_non_image_bytes_are_never_served(student):
    product = ReadyProduct.objects.create(name="x", price=Decimal("1"))
    product.image.save("p.png", ContentFile(b"<html>not an image</html>"))
    assert student.get(f"/api/v1/printing/products/{product.pk}/image/").status_code == 404


def test_inactive_hidden_from_students_but_visible_to_staff(student, staff):
    product = _product(is_active=False)
    url = f"/api/v1/printing/products/{product.pk}/image/"
    assert student.get(url).status_code == 404
    assert staff.get(url).status_code == 200


def test_deleted_and_missing_image_are_404_for_everyone(student, staff):
    deleted = _product(is_deleted=True)
    plain = _product(image=False)
    for client in (student, staff):
        assert client.get(f"/api/v1/printing/products/{deleted.pk}/image/").status_code == 404
        assert client.get(f"/api/v1/printing/products/{plain.pk}/image/").status_code == 404
        assert client.get("/api/v1/printing/products/99999/image/").status_code == 404


def test_serializers_return_the_authenticated_route_not_media(student, staff):
    product = _product()
    plain = _product(image=False)
    listing = student.get("/api/v1/printing/products/").json()["data"]
    rows = {row["id"]: row for row in (listing["results"] if isinstance(listing, dict) else listing)}
    assert f"/api/v1/printing/products/{product.pk}/image/?v=" in rows[product.pk]["image_url"]
    assert rows[plain.pk]["image_url"] is None
    detail = student.get(f"/api/v1/printing/products/{product.pk}/").json()["data"]
    assert f"/api/v1/printing/products/{product.pk}/image/?v=" in detail["image_url"]

    staff_row = staff.get(f"/api/v1/dashboard/printing/products/{product.pk}/").json()["data"]
    expected = f"/api/v1/printing/products/{product.pk}/image/?v="
    assert expected in staff_row["image_url"] and expected in staff_row["image"]
    assert "/media/" not in staff_row["image_url"] + staff_row["image"]
    staff_plain = staff.get(f"/api/v1/dashboard/printing/products/{plain.pk}/").json()["data"]
    assert staff_plain["image"] is None and staff_plain["image_url"] is None
