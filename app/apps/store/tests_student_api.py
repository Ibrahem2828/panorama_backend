"""Student API: catalogue, cart, checkout (stock, rounding, idempotency), orders, reviews, notifications."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from django.core.files.base import ContentFile

from apps.audit.models import AuditAction, AuditLog
from apps.finance.models import StockMovement, StockMovementType
from apps.notifications.models import Notification

from .models import StoreBrand, StoreCart, StoreOrder, StoreOrderStatus, StoreProduct, StoreProductImage, StoreVariant
from .test_support import client_for, make_variant, png_bytes

pytestmark = pytest.mark.django_db

API = "/api/v1/store"


def _data(response):
    return response.json()["data"]


def _add(client, variant, quantity=1):
    return client.post(f"{API}/cart/items/", {"variant": variant.pk, "quantity": quantity}, format="json")


def _checkout(client, currency="SYP", **extra):
    return client.post(f"{API}/checkout/", {"currency": currency, **extra}, format="json")


def _picked_up_order(student, variant, quantity=1):
    order = StoreOrder.objects.create(
        user=student, status=StoreOrderStatus.PICKED_UP, currency="SYP", subtotal=1, total=1
    )
    order.items.create(
        product=variant.product, variant=variant, name="x", unit_price=1, quantity=quantity, line_total=1
    )
    return order


# ---- catalogue --------------------------------------------------------------------------------------------------------------------


def test_anonymous_gets_401(db):
    from rest_framework.test import APIClient

    for path in ("/products/", "/brands/", "/categories/", "/cart/", "/orders/"):
        assert APIClient().get(f"{API}{path}").status_code == 401


def test_product_list_card_fields_and_filters(student, product, variant, brand, category):
    other = StoreProduct.objects.create(name="دفتر", category=category, price_syp=Decimal("500"))
    make_variant(other, stock=0, colour_name="")
    client = client_for(student)
    rows = _data(client.get(f"{API}/products/"))["results"]
    assert {row["name"] for row in rows} == {"قلم جاف", "دفتر"}
    pen = next(row for row in rows if row["name"] == "قلم جاف")
    assert pen["brand"] == {"id": brand.pk, "name": "Pilot"}
    assert pen["price_from"] == {"SYP": "1003.00", "USD": "2.50"}
    assert pen["in_stock"] is True and pen["rating_count"] == 0 and pen["image_url"] is None
    assert next(row for row in rows if row["name"] == "دفتر")["in_stock"] is False
    assert [r["name"] for r in _data(client.get(f"{API}/products/?in_stock=true"))["results"]] == ["قلم جاف"]
    assert [r["name"] for r in _data(client.get(f"{API}/products/?brand={brand.pk}"))["results"]] == ["قلم جاف"]
    assert [r["name"] for r in _data(client.get(f"{API}/products/?category={category.pk}"))["results"]] == [
        r["name"] for r in _data(client.get(f"{API}/products/?category={category.pk}"))["results"]
    ]
    assert [r["name"] for r in _data(client.get(f"{API}/products/?search=Pilot"))["results"]] == ["قلم جاف"]
    assert [r["name"] for r in _data(client.get(f"{API}/products/?search=PEN-RED"))["results"]] == ["قلم جاف"]
    assert _data(client.get(f"{API}/products/?search=zzz"))["count"] == 0


def test_product_ordering_by_price_popularity_and_newest(student, product, variant, category):
    cheap = StoreProduct.objects.create(name="رخيص", category=category, price_syp=Decimal("10"), sold_count=50)
    make_variant(cheap)
    client = client_for(student)

    def names(query):
        return [r["name"] for r in _data(client.get(f"{API}/products/?{query}"))["results"]]

    assert names("ordering=price_asc") == ["رخيص", "قلم جاف"]
    assert names("ordering=price_desc") == ["قلم جاف", "رخيص"]
    assert names("ordering=popular") == ["رخيص", "قلم جاف"]
    assert names("ordering=newest") == ["رخيص", "قلم جاف"]
    assert names("ordering=rating&currency=USD")  # accepted
    assert names("ordering=price_asc&currency=USD")[-1] == "رخيص"  # no USD price sorts last
    assert names("ordering=bogus")  # unknown ordering falls back to popular


def test_pagination(student, product, category):
    for n in range(25):
        StoreProduct.objects.create(name=f"p{n}", category=category)
    data = _data(client_for(student).get(f"{API}/products/?page_size=10"))
    assert data["count"] == 26 and len(data["results"]) == 10 and data["total_pages"] == 3


def test_inactive_and_deleted_catalogue_is_hidden(student, product, variant, brand, category):
    client = client_for(student)
    assert _data(client.get(f"{API}/brands/")) and _data(client.get(f"{API}/categories/"))
    brand.is_active = False
    brand.save()
    assert _data(client.get(f"{API}/brands/")) == []
    assert _data(client.get(f"{API}/products/"))["count"] == 0
    assert client.get(f"{API}/products/{product.pk}/").status_code == 404
    brand.is_active = True
    brand.save()
    category.is_active = False
    category.save()
    assert client.get(f"{API}/products/{product.pk}/").status_code == 404
    assert _data(client.get(f"{API}/categories/")) == []
    category.is_active = True
    category.save()
    product.is_deleted = True
    product.save()
    assert client.get(f"{API}/products/{product.pk}/").status_code == 404


def test_brands_without_products_are_not_listed(student, brand):
    assert _data(client_for(student).get(f"{API}/brands/")) == []


def test_product_detail_has_variants_images_specs_and_authenticated_urls(student, product, variant):
    image = StoreProductImage.objects.create(product=product, is_primary=True, variant=variant)
    image.image.save("x.png", ContentFile(png_bytes()), save=True)
    make_variant(product, colour_name="أزرق", colour_hex="#0000FF", size_label="M", price_syp=Decimal("1500"))
    hidden = make_variant(product, colour_name="مخفي")
    hidden.is_active = False
    hidden.save()
    detail = _data(client_for(student).get(f"{API}/products/{product.pk}/"))
    assert detail["specs"] == [{"label": "الحبر", "value": "أزرق"}]
    assert detail["description"] == "قلم ناعم" and detail["can_review"] is False
    assert len(detail["variants"]) == 2
    blue = next(v for v in detail["variants"] if v["colour_name"] == "أزرق")
    assert blue["prices"] == {"SYP": "1500.00", "USD": "2.50"} and blue["colour_hex"] == "#0000FF"
    assert blue["available"] == 10 and blue["in_stock"] is True and blue["max_per_order"] == 20
    url = detail["images"][0]["url"]
    assert f"/api/v1/store/images/{image.pk}/?v=" in url and "/media/" not in url
    assert detail["images"][0]["thumb_url"].endswith("&variant=thumb")
    assert detail["thumb_url"] == detail["images"][0]["thumb_url"]


# ---- cart -------------------------------------------------------------------------------------------------------------------------


def test_cart_add_merge_update_remove_clear(student, product, variant):
    client = client_for(student)
    first = _add(client, variant, 2)
    assert first.status_code == 201
    cart = _data(first)
    assert cart["items_count"] == 2 and cart["payment"] == "cash_on_pickup"
    assert cart["totals"]["SYP"] == {"subtotal": "2006.00", "rounding_adjustment": "4.00", "total": "2010.00"}
    assert cart["totals"]["USD"]["total"] == "5.00"
    merged = _data(_add(client, variant, 3))
    assert merged["items"][0]["quantity"] == 5 and len(merged["items"]) == 1
    item_id = merged["items"][0]["id"]
    assert _data(client.patch(f"{API}/cart/items/{item_id}/", {"quantity": 1}, format="json"))["items_count"] == 1
    assert _data(client.get(f"{API}/cart/"))["items"][0]["orderable"] is True
    assert _data(client.delete(f"{API}/cart/items/{item_id}/"))["items"] == []
    _add(client, variant)
    assert _data(client.delete(f"{API}/cart/"))["items_count"] == 0


def test_cart_quantity_rules(student, product, variant):
    client = client_for(student)
    over_stock = _add(client, variant, 11)
    assert over_stock.status_code == 409
    body = over_stock.json()
    assert body["code"] == "INSUFFICIENT_STOCK" and body["errors"]["available"] == 10
    variant.max_per_order = 3
    variant.save()
    capped = _add(client, variant, 4)
    assert capped.status_code == 400 and capped.json()["code"] == "QUANTITY_LIMIT"
    assert _add(client, variant, 3).status_code == 201
    assert _add(client, variant, 1).json()["code"] == "QUANTITY_LIMIT"  # merged line would exceed the cap
    assert _add(client, variant, 0).status_code == 400
    assert (
        client.patch(f"{API}/cart/items/999999/", {"quantity": 1}, format="json").json()["code"]
        == "CART_ITEM_NOT_FOUND"
    )


def test_cart_refuses_unavailable_variants_and_full_carts(student, product, variant):
    client = client_for(student)
    variant.is_active = False
    variant.save()
    refused = _add(client, variant)
    assert refused.status_code == 404 and refused.json()["code"] == "VARIANT_UNAVAILABLE"
    assert (
        _add(client, make_variant(StoreProduct.objects.create(name="x", category=product.category), 0)).status_code
        == 409
    )
    from . import services

    for n in range(services.MAX_CART_LINES):
        extra = make_variant(product, colour_name=f"c{n}")
        assert _add(client, extra).status_code == 201
    over = _add(client, make_variant(product, colour_name="last"))
    assert over.status_code == 400 and over.json()["code"] == "CART_FULL"


def test_carts_are_private_per_user(student, other_student, variant):
    _add(client_for(student), variant)
    assert _data(client_for(other_student).get(f"{API}/cart/"))["items"] == []
    item = StoreCart.objects.get(user=student).items.get()
    other = client_for(other_student)
    assert other.patch(f"{API}/cart/items/{item.pk}/", {"quantity": 2}, format="json").status_code == 404
    assert other.delete(f"{API}/cart/items/{item.pk}/").status_code == 404
    item.refresh_from_db()
    assert item.quantity == 1


# ---- checkout ---------------------------------------------------------------------------------------------------------------------


def test_checkout_creates_a_pending_order_takes_stock_and_clears_the_cart(student, variant, staff):
    client = client_for(student)
    _add(client, variant, 2)
    response = _checkout(client, "SYP", notes=" بدي ألوان ")
    assert response.status_code == 201, response.content
    order = _data(response)
    assert order["status"] == "pending" and order["code"] == f"ST-{order['id']:06d}"
    assert order["subtotal"] == "2006.00" and order["total"] == "2010.00" and order["rounding_adjustment"] == "4.00"
    assert order["notes"] == "بدي ألوان" and order["can_cancel"] is True and order["payment"] == "cash_on_pickup"
    line = order["items"][0]
    assert line["name"] == "قلم جاف" and line["brand_name"] == "Pilot" and line["unit_price"] == "1003.00"
    assert line["line_total"] == "2006.00" and line["variant_label"] == "أحمر"
    assert order["history"][0]["new_status"] == "pending"
    variant.shelf.refresh_from_db()
    assert variant.shelf.quantity == 8
    movement = StockMovement.objects.filter(item=variant.shelf, movement_type=StockMovementType.USAGE).get()
    assert movement.quantity == -2 and movement.created_by == student
    assert StoreCart.objects.get(user=student).items.count() == 0
    assert AuditLog.objects.filter(action=AuditAction.STORE_ORDER_CREATED, actor=student).count() == 1


def test_checkout_snapshots_prices(student, variant):
    client = client_for(student)
    _add(client, variant)
    order_id = _data(_checkout(client))["id"]
    variant.price_syp = Decimal("9999")
    variant.save()
    variant.product.name = "اسم جديد"
    variant.product.save()
    detail = _data(client.get(f"{API}/orders/{order_id}/"))
    assert detail["items"][0]["unit_price"] == "1003.00" and detail["items"][0]["name"] == "قلم جاف"


def test_checkout_in_usd_keeps_cents_and_refuses_unpriced_lines(student, product, variant):
    client = client_for(student)
    _add(client, variant, 3)
    usd = _data(_checkout(client, "USD"))
    assert usd["currency"] == "USD" and usd["total"] == "7.50" and usd["rounding_adjustment"] == "0.00"
    syp_only = make_variant(product, colour_name="فقط ليرة", price_usd=None)
    product.price_usd = None
    product.save()
    _add(client, syp_only)
    refused = _checkout(client, "USD")
    assert refused.status_code == 400 and refused.json()["code"] == "PRICE_UNAVAILABLE"
    assert _data(client.get(f"{API}/cart/"))["totals"]["USD"] is None
    assert _checkout(client, "SYP").status_code == 201


def test_checkout_validation(student, variant):
    client = client_for(student)
    assert _checkout(client).json()["code"] == "CART_EMPTY"
    _add(client, variant)
    assert _checkout(client, "EUR").status_code == 400
    assert client.post(f"{API}/checkout/", {}, format="json").status_code == 400


def test_checkout_refuses_when_stock_ran_out_and_changes_nothing(student, variant):
    client = client_for(student)
    _add(client, variant, 5)
    variant.shelf.quantity = Decimal("3")
    variant.shelf.save()
    refused = _checkout(client)
    assert refused.status_code == 409 and refused.json()["code"] == "INSUFFICIENT_STOCK"
    assert refused.json()["errors"]["variant"] == variant.pk and refused.json()["errors"]["available"] == 3
    assert StoreOrder.objects.count() == 0
    variant.shelf.refresh_from_db()
    assert variant.shelf.quantity == 3
    assert StoreCart.objects.get(user=student).items.count() == 1  # the cart is kept so the student can adjust it


def test_two_students_cannot_oversell_the_last_units(student, other_student, variant):
    a, b = client_for(student), client_for(other_student)
    _add(a, variant, 6)
    _add(b, variant, 6)
    assert _checkout(a).status_code == 201
    second = _checkout(b)
    assert second.status_code == 409 and second.json()["errors"]["available"] == 4
    variant.shelf.refresh_from_db()
    assert variant.shelf.quantity == 4 and StoreOrder.objects.count() == 1


def test_checkout_refuses_items_withdrawn_after_they_were_carted(student, variant):
    client = client_for(student)
    _add(client, variant)
    variant.product.is_active = False
    variant.product.save()
    assert _data(client.get(f"{API}/cart/"))["items"][0]["orderable"] is False
    assert _checkout(client).json()["code"] == "VARIANT_UNAVAILABLE"


def test_open_order_limit(student, variant):
    from . import services

    client = client_for(student)
    for _ in range(services.MAX_OPEN_ORDERS):
        _add(client, variant)
        assert _checkout(client).status_code == 201
    _add(client, variant)
    blocked = _checkout(client)
    assert blocked.status_code == 409 and blocked.json()["code"] == "TOO_MANY_OPEN_ORDERS"


def test_checkout_is_idempotent(student, variant):
    client = client_for(student)
    _add(client, variant, 2)
    first = client.post(f"{API}/checkout/", {"currency": "SYP"}, format="json", HTTP_IDEMPOTENCY_KEY="key-1")
    replay = client.post(f"{API}/checkout/", {"currency": "SYP"}, format="json", HTTP_IDEMPOTENCY_KEY="key-1")
    assert first.status_code == replay.status_code == 201
    assert _data(first)["id"] == _data(replay)["id"]
    assert StoreOrder.objects.count() == 1
    variant.shelf.refresh_from_db()
    assert variant.shelf.quantity == 8  # the stock was taken once
    other_body = client.post(f"{API}/checkout/", {"currency": "USD"}, format="json", HTTP_IDEMPOTENCY_KEY="key-1")
    assert other_body.status_code == 409 and other_body.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_a_rejected_checkout_releases_its_idempotency_key(student, variant):
    client = client_for(student)
    headers: dict[str, Any] = {"HTTP_IDEMPOTENCY_KEY": "key-2"}
    assert client.post(f"{API}/checkout/", {"currency": "SYP"}, format="json", **headers).json()["code"] == "CART_EMPTY"
    _add(client, variant)
    assert client.post(f"{API}/checkout/", {"currency": "SYP"}, format="json", **headers).status_code == 201


def test_checkout_notifies_the_student_and_the_store_staff(
    student, staff, support_staff, variant, django_capture_on_commit_callbacks
):
    client = client_for(student)
    _add(client, variant)
    with django_capture_on_commit_callbacks(execute=True):
        order_id = _data(_checkout(client))["id"]
    mine = Notification.objects.get(user=student)
    assert mine.data["kind"] == "store_order_status" and mine.data["store_order_id"] == order_id
    assert "نقداً" in mine.body
    staff_note = Notification.objects.get(user=staff)
    assert staff_note.data["kind"] == "store_order" and staff_note.deep_link.endswith(f"highlight={order_id}")
    assert not Notification.objects.filter(user=support_staff).exists()  # no store.manage, no inbox entry


# ---- orders and cancellation ------------------------------------------------------------------------------------------------------


def test_my_orders_list_detail_and_filter(student, other_student, variant):
    client = client_for(student)
    _add(client, variant)
    order_id = _data(_checkout(client))["id"]
    _add(client_for(other_student), variant)
    _checkout(client_for(other_student))
    listing = _data(client.get(f"{API}/orders/"))
    assert (
        listing["count"] == 1 and listing["results"][0]["id"] == order_id and listing["results"][0]["items_count"] == 1
    )
    assert _data(client.get(f"{API}/orders/?status=picked_up"))["count"] == 0
    assert _data(client.get(f"{API}/orders/?status=pending"))["count"] == 1
    assert _data(client.get(f"{API}/orders/{order_id}/"))["id"] == order_id
    assert client_for(other_student).get(f"{API}/orders/{order_id}/").status_code == 404


def test_student_cancels_a_pending_order_and_the_stock_comes_back(student, variant):
    client = client_for(student)
    _add(client, variant, 4)
    order_id = _data(_checkout(client))["id"]
    variant.shelf.refresh_from_db()
    assert variant.shelf.quantity == 6
    cancelled = client.post(f"{API}/orders/{order_id}/cancel/", {"reason": "غيرت رأيي"}, format="json")
    assert cancelled.status_code == 200, cancelled.content
    data = _data(cancelled)
    assert data["status"] == "cancelled" and data["cancelled_by_customer"] is True and data["can_cancel"] is False
    variant.shelf.refresh_from_db()
    assert variant.shelf.quantity == 10
    assert StockMovement.objects.filter(
        item=variant.shelf, movement_type=StockMovementType.ADJUSTMENT, quantity=4
    ).exists()
    again = client.post(f"{API}/orders/{order_id}/cancel/", {}, format="json")
    assert again.status_code == 409 and again.json()["code"] == "ORDER_NOT_CANCELLABLE"
    variant.shelf.refresh_from_db()
    assert variant.shelf.quantity == 10  # never returned twice
    assert AuditLog.objects.filter(action=AuditAction.STORE_ORDER_STATUS_CHANGED, actor=student).count() == 1


def test_student_cannot_cancel_once_confirmed_or_someone_elses_order(student, other_student, variant):
    client = client_for(student)
    _add(client, variant)
    order_id = _data(_checkout(client))["id"]
    assert client_for(other_student).post(f"{API}/orders/{order_id}/cancel/", {}, format="json").status_code == 404
    StoreOrder.objects.filter(pk=order_id).update(status=StoreOrderStatus.CONFIRMED)
    assert client.post(f"{API}/orders/{order_id}/cancel/", {}, format="json").status_code == 409


# ---- reviews ----------------------------------------------------------------------------------------------------------------------


def test_only_students_who_picked_the_product_up_can_review(student, other_student, product, variant):
    client = client_for(student)
    url = f"{API}/products/{product.pk}/reviews/"
    refused = client.post(url, {"rating": 5}, format="json")
    assert refused.status_code == 403 and refused.json()["code"] == "REVIEW_NOT_ELIGIBLE"
    pending = StoreOrder.objects.create(
        user=student, status=StoreOrderStatus.READY, currency="SYP", subtotal=1, total=1
    )
    pending.items.create(product=product, variant=variant, name="x", unit_price=1, quantity=1, line_total=1)
    assert client.post(url, {"rating": 5}, format="json").status_code == 403  # ready is not received yet
    _picked_up_order(student, variant)
    assert _data(client.get(f"{API}/products/{product.pk}/"))["can_review"] is True
    created = client.post(url, {"rating": 4, "text": "ممتاز"}, format="json")
    assert created.status_code == 201 and _data(created)["author"] == "Store"
    assert client_for(other_student).post(url, {"rating": 5}, format="json").status_code == 403  # IDOR: another's order
    assert client.post(url, {"rating": 1}, format="json").json()["code"] == "REVIEW_EXISTS"


def test_rating_validation(student, product, variant):
    _picked_up_order(student, variant)
    client = client_for(student)
    url = f"{API}/products/{product.pk}/reviews/"
    for bad in (0, 6, "x"):
        assert client.post(url, {"rating": bad}, format="json").status_code == 400
    assert client.post(url, {"rating": 3, "text": "x" * 1001}, format="json").status_code == 400


def test_review_summary_edit_once_and_listing(student, other_student, product, variant):
    _picked_up_order(student, variant)
    _picked_up_order(other_student, variant)
    a, b = client_for(student), client_for(other_student)
    url = f"{API}/products/{product.pk}/reviews/"
    a.post(url, {"rating": 5, "text": "رائع"}, format="json")
    b.post(url, {"rating": 2}, format="json")
    product.refresh_from_db()
    assert product.rating_count == 2 and product.rating_avg == Decimal("3.50")
    card = _data(a.get(f"{API}/products/"))["results"][0]
    assert card["rating_avg"] == "3.50" and card["rating_count"] == 2
    edited = a.patch(f"{url}mine/", {"rating": 1}, format="json")
    assert edited.status_code == 200 and _data(edited)["edited"] is True and _data(edited)["text"] == "رائع"
    product.refresh_from_db()
    assert product.rating_avg == Decimal("1.50")
    second_edit = a.patch(f"{url}mine/", {"rating": 5}, format="json")
    assert second_edit.status_code == 409 and second_edit.json()["code"] == "REVIEW_EDIT_LIMIT"
    assert _data(a.get(f"{url}mine/"))["rating"] == 1
    assert a.patch(f"{url}mine/", {}, format="json").status_code == 400
    listing = _data(a.get(url))
    assert listing["count"] == 2 and {r["rating"] for r in listing["results"]} == {1, 2}
    assert "user" not in listing["results"][0] and "email" not in str(listing)


def test_edit_without_a_review_is_404(student, product, variant):
    assert (
        client_for(student)
        .patch(f"{API}/products/{product.pk}/reviews/mine/", {"rating": 3}, format="json")
        .status_code
        == 404
    )
    assert client_for(student).get(f"{API}/products/{product.pk}/reviews/mine/").status_code == 404


def test_reviews_of_hidden_products_are_not_reachable(student, product, variant):
    product.is_active = False
    product.save()
    assert client_for(student).get(f"{API}/products/{product.pk}/reviews/").status_code == 404


# ---- order pick-up feeds popularity -----------------------------------------------------------------------------------------------


def test_picking_up_raises_the_sold_count(student, staff, variant):
    client = client_for(student)
    _add(client, variant, 3)
    order_id = _data(_checkout(client))["id"]
    staff_client = client_for(staff)
    for target in ("confirmed", "ready_for_pickup", "picked_up"):
        assert (
            staff_client.patch(
                f"/api/v1/dashboard/store/orders/{order_id}/status/", {"status": target}, format="json"
            ).status_code
            == 200
        )
    variant.product.refresh_from_db()
    assert variant.product.sold_count == 3
    assert StoreProduct.objects.get(pk=variant.product_id).rating_count == 0
    assert StoreBrand.objects.count() == 1 and StoreVariant.objects.count() == 1
