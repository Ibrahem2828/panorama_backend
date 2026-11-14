"""Authenticated image delivery (no /media/), IDOR on carts/orders/reviews/images and throttles."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.files.base import ContentFile
from rest_framework.test import APIClient

from apps.common.throttles import StoreCartRateThrottle, StoreCheckoutRateThrottle, StoreReviewRateThrottle

from . import services
from .models import StoreBrand, StoreOrder, StoreProductImage
from .test_support import client_for, png_bytes

pytestmark = pytest.mark.django_db

API = "/api/v1/store"


def _body(response) -> bytes:
    return b"".join(response.streaming_content)


@pytest.fixture
def picture(product):
    image = StoreProductImage.objects.create(product=product, is_primary=True)
    image.image.save("x.png", ContentFile(png_bytes((900, 700))), save=True)
    services.attach_thumbnail(image)
    return image


def test_images_need_a_login(picture):
    assert APIClient().get(f"{API}/images/{picture.pk}/").status_code == 401
    assert APIClient().get(f"{API}/brands/1/logo/").status_code == 401


def test_original_and_thumbnail_headers(student, picture):
    client = client_for(student)
    original = client.get(f"{API}/images/{picture.pk}/")
    assert original.status_code == 200 and original["Content-Type"] == "image/png"
    assert original["Cache-Control"].startswith("private") and original["X-Content-Type-Options"] == "nosniff"
    assert original["ETag"]
    assert _body(original).startswith(b"\x89PNG")
    thumb = client.get(f"{API}/images/{picture.pk}/?variant=thumb&v=123")
    assert thumb.status_code == 200 and thumb["Content-Type"] == "image/webp"
    assert thumb["ETag"] != original["ETag"]
    from io import BytesIO

    from PIL import Image

    body = _body(thumb)
    assert max(Image.open(BytesIO(body)).size) <= 480


def test_etag_gives_304_and_changes_with_the_file(student, picture):
    client = client_for(student)
    etag = client.get(f"{API}/images/{picture.pk}/")["ETag"]
    cached = client.get(f"{API}/images/{picture.pk}/", HTTP_IF_NONE_MATCH=etag)
    assert cached.status_code == 304 and cached["ETag"] == etag and not cached.content
    stale = client.get(f"{API}/images/{picture.pk}/", HTTP_IF_NONE_MATCH='"other"')
    assert stale.status_code == 200
    picture.image.save("y.png", ContentFile(png_bytes((10, 10), "blue")), save=True)
    assert client.get(f"{API}/images/{picture.pk}/")["ETag"] != etag


def test_thumb_falls_back_to_the_original_when_missing(student, picture):
    picture.thumb = None
    picture.save()
    response = client_for(student).get(f"{API}/images/{picture.pk}/?variant=thumb")
    assert response.status_code == 200 and response["Content-Type"] == "image/png"


def test_only_real_images_are_served(student, picture):
    picture.image.save("evil.png", ContentFile(b"<html>not an image</html>"), save=True)
    assert client_for(student).get(f"{API}/images/{picture.pk}/").status_code == 404


def test_missing_file_and_unknown_id_are_404(student, picture):
    picture.image.storage.delete(picture.image.name)
    client = client_for(student)
    assert client.get(f"{API}/images/{picture.pk}/").status_code == 404
    assert client.get(f"{API}/images/999999/").status_code == 404


def test_hidden_catalogue_images_are_only_for_store_staff(student, staff, support_staff, product, picture):
    product.is_active = False
    product.save()
    assert client_for(student).get(f"{API}/images/{picture.pk}/").status_code == 404
    assert client_for(support_staff).get(f"{API}/images/{picture.pk}/").status_code == 404
    assert client_for(staff).get(f"{API}/images/{picture.pk}/").status_code == 200  # dashboard preview
    product.is_active = True
    product.save()
    picture.is_deleted = True
    picture.save()
    assert client_for(staff).get(f"{API}/images/{picture.pk}/").status_code == 404


def test_brand_logo_delivery(student, staff, brand):
    brand.logo.save("l.png", ContentFile(png_bytes()), save=True)
    client = client_for(student)
    url = f"{API}/brands/{brand.pk}/logo/"
    response = client.get(url)
    assert response.status_code == 200 and response["Content-Type"] == "image/png"
    assert client.get(url, HTTP_IF_NONE_MATCH=response["ETag"]).status_code == 304
    brand.is_active = False
    brand.save()
    assert client.get(url).status_code == 404
    assert client_for(staff).get(url).status_code == 200
    assert client.get(f"{API}/brands/999999/logo/").status_code == 404
    assert (
        StoreBrand.objects.create(name="NoLogo")
        and client.get(f"{API}/brands/{StoreBrand.objects.get(name='NoLogo').pk}/logo/").status_code == 404
    )


def test_urls_in_the_api_never_point_at_media(student, product, variant, picture):
    text = client_for(student).get(f"{API}/products/{product.pk}/").content.decode()
    assert "/media/" not in text and "/api/v1/store/images/" in text


# ---- IDOR -------------------------------------------------------------------------------------------------------------------------


def test_student_b_cannot_touch_student_a_objects(student, other_student, variant, product):
    a, b = client_for(student), client_for(other_student)
    a.post(f"{API}/cart/items/", {"variant": variant.pk, "quantity": 1}, format="json")
    order_id = a.post(f"{API}/checkout/", {"currency": "SYP"}, format="json").json()["data"]["id"]
    assert a.get(f"{API}/orders/{order_id}/").status_code == 200
    assert b.get(f"{API}/orders/{order_id}/").status_code == 404
    assert b.post(f"{API}/orders/{order_id}/cancel/", {}, format="json").status_code == 404
    assert b.get(f"{API}/orders/").json()["data"]["count"] == 0
    assert StoreOrder.objects.get(pk=order_id).status == "pending"
    # reviews: B has no pick-up, so cannot review, and A's review is not editable by B
    StoreOrder.objects.filter(pk=order_id).update(status="picked_up")
    assert a.post(f"{API}/products/{product.pk}/reviews/", {"rating": 5}, format="json").status_code == 201
    assert b.patch(f"{API}/products/{product.pk}/reviews/mine/", {"rating": 1}, format="json").status_code == 404
    assert b.get(f"{API}/products/{product.pk}/reviews/mine/").status_code == 404


def test_staff_dashboard_cannot_be_reached_with_student_credentials(student, variant):
    order = StoreOrder.objects.create(user=student, currency="SYP", subtotal=1, total=1)
    client = client_for(student)
    assert (
        client.patch(
            f"/api/v1/dashboard/store/orders/{order.pk}/status/", {"status": "confirmed"}, format="json"
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/dashboard/store/variants/{variant.pk}/stock/",
            {"movement_type": "purchase", "quantity": 5},
            format="json",
        ).status_code
        == 403
    )
    assert client.post("/api/v1/dashboard/store/reviews/1/hide/", {"reason": "x"}, format="json").status_code == 403


# ---- throttles --------------------------------------------------------------------------------------------------------------------


def test_cart_checkout_and_review_throttles(student, other_student, product, variant):
    client = client_for(student)
    with patch.object(StoreCartRateThrottle, "THROTTLE_RATES", {"store_cart": "2/min"}):
        assert client.get(f"{API}/cart/").status_code == 200
        assert client.get(f"{API}/cart/").status_code == 200
        limited = client.get(f"{API}/cart/")
        assert limited.status_code == 429 and limited.json()["code"] == "RATE_LIMITED"
    client = client_for(other_student)
    with patch.object(StoreCheckoutRateThrottle, "THROTTLE_RATES", {"store_checkout": "1/min"}):
        assert (
            client.post(f"{API}/checkout/", {"currency": "SYP"}, format="json").status_code == 400
        )  # empty cart counts
        assert client.post(f"{API}/checkout/", {"currency": "SYP"}, format="json").status_code == 429
    with patch.object(StoreReviewRateThrottle, "THROTTLE_RATES", {"store_review": "1/min"}):
        url = f"{API}/products/{product.pk}/reviews/"
        assert client.post(url, {"rating": 5}, format="json").status_code == 403
        assert client.post(url, {"rating": 5}, format="json").status_code == 429
        assert client.get(url).status_code == 200  # reading is not throttled by the write limit
