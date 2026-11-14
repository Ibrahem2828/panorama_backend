"""Dashboard API: capability gate, catalogue CRUD with audit, stock, order workflow, review moderation, summary."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.accounts.choices import UserRole
from apps.accounts.permissions import ROLE_CAPABILITIES, Capability, PermissionService
from apps.audit.models import AuditAction, AuditLog
from apps.finance.models import StockMovement
from apps.notifications.models import Notification

from .models import StoreBrand, StoreCategory, StoreOrder, StoreOrderStatus, StoreProduct, StoreReview, StoreVariant
from .test_support import client_for, make_user, make_variant, png_upload

pytestmark = pytest.mark.django_db

DASH = "/api/v1/dashboard/store"
API = "/api/v1/store"


def _data(response):
    return response.json()["data"]


def _order(student, variant, quantity=2):
    client = client_for(student)
    client.post(f"{API}/cart/items/", {"variant": variant.pk, "quantity": quantity}, format="json")
    return _data(client.post(f"{API}/checkout/", {"currency": "SYP"}, format="json"))["id"]


def _move(client, order_id, status, **extra):
    return client.patch(f"{DASH}/orders/{order_id}/status/", {"status": status, **extra}, format="json")


# ---- capability -------------------------------------------------------------------------------------------------------------------


def test_store_manage_is_granted_to_admin_it_and_print_staff_only(admin, staff, support_staff, student):
    assert Capability.STORE_MANAGE == "store.manage"
    assert Capability.STORE_MANAGE in ROLE_CAPABILITIES[UserRole.ADMIN]
    assert Capability.STORE_MANAGE in ROLE_CAPABILITIES[UserRole.PRINT_STAFF]
    assert "*" in ROLE_CAPABILITIES[UserRole.IT_SUPPORT]
    assert PermissionService.has(admin, "store.manage") and PermissionService.has(staff, "store.manage")
    assert not PermissionService.has(support_staff, "store.manage") and not PermissionService.has(
        student, "store.manage"
    )
    content = make_user(UserRole.CONTENT_MANAGER, 9)
    assert not PermissionService.has(content, "store.manage")


def test_the_capability_is_listed_for_overrides():
    from apps.accounts.dashboard_serializers import ALL_CAPABILITIES

    assert "store.manage" in ALL_CAPABILITIES


@pytest.mark.parametrize(
    "path", ["brands/", "categories/", "products/", "variants/", "images/", "orders/", "reviews/", "summary/"]
)
def test_students_and_support_staff_are_refused(student, support_staff, path):
    assert client_for(student).get(f"{DASH}/{path}").status_code == 403
    assert client_for(support_staff).get(f"{DASH}/{path}").status_code == 403


# ---- catalogue --------------------------------------------------------------------------------------------------------------------


def test_brand_crud_with_logo_audit_and_delete_guard(staff, category):
    client = client_for(staff)
    created = client.post(
        f"{DASH}/brands/", {"name": " Staedtler ", "logo": png_upload("logo.png")}, format="multipart"
    )
    assert created.status_code == 201, created.content
    brand = _data(created)
    assert brand["name"] == "Staedtler" and "/api/v1/store/brands/" in brand["logo_url"] and "logo" not in brand
    assert client.post(f"{DASH}/brands/", {"name": "staedtler"}, format="json").status_code == 400
    assert client.patch(f"{DASH}/brands/{brand['id']}/", {"sort_order": 3}, format="json").status_code == 200
    StoreProduct.objects.create(name="p", category=category, brand_id=brand["id"])
    blocked = client.delete(f"{DASH}/brands/{brand['id']}/")
    assert blocked.status_code == 409 and blocked.json()["code"] == "IN_USE"
    StoreProduct.objects.all().update(is_deleted=True)
    assert client.delete(f"{DASH}/brands/{brand['id']}/").status_code == 200
    assert StoreBrand.objects.get(pk=brand["id"]).is_deleted
    actions = list(AuditLog.objects.filter(actor=staff).values_list("action", flat=True))
    assert actions.count(AuditAction.STORE_CATALOG_CHANGED) == 3  # create, update, delete


def test_logo_must_be_a_real_image(staff):
    from django.core.files.uploadedfile import SimpleUploadedFile

    fake = SimpleUploadedFile("logo.png", b"not an image at all", content_type="image/png")
    assert client_for(staff).post(f"{DASH}/brands/", {"name": "X", "logo": fake}, format="multipart").status_code == 400


def test_category_crud_and_guard(staff):
    client = client_for(staff)
    made = _data(client.post(f"{DASH}/categories/", {"name": "حقائب", "slug": "bags"}, format="json"))
    assert client.post(f"{DASH}/categories/", {"name": "x", "slug": "bags"}, format="json").status_code == 400
    assert client.patch(f"{DASH}/categories/{made['id']}/", {"name": "شنط"}, format="json").status_code == 200
    StoreProduct.objects.create(name="p", category_id=made["id"])
    assert client.delete(f"{DASH}/categories/{made['id']}/").status_code == 409
    StoreProduct.objects.all().update(is_deleted=True)
    assert client.delete(f"{DASH}/categories/{made['id']}/").status_code == 200
    assert not StoreCategory.objects.filter(pk=made["id"], is_deleted=False).exists()


def test_product_crud_validation_and_price_audit(staff, brand, category):
    client = client_for(staff)
    payload = {
        "name": "دفتر",
        "brand": brand.pk,
        "category": category.pk,
        "price_syp": "5000",
        "price_usd": "1.20",
        "specs": [{"label": "الورق", "value": "80 غرام"}],
    }
    created = client.post(f"{DASH}/products/", payload, format="json")
    assert created.status_code == 201, created.content
    pid = _data(created)["id"]
    assert AuditLog.objects.filter(action=AuditAction.STORE_CATALOG_CHANGED, target_id=str(pid)).count() == 1
    for bad in (
        {"specs": "x"},
        {"specs": [{"label": "", "value": "v"}]},
        {"specs": [{"label": "a", "value": "b", "x": 1}]},
        {"price_syp": "0"},
        {"name": " "},
    ):
        assert client.patch(f"{DASH}/products/{pid}/", bad, format="json").status_code == 400, bad
    client.patch(f"{DASH}/products/{pid}/", {"description": "وصف"}, format="json")
    assert not AuditLog.objects.filter(action=AuditAction.STORE_PRICE_CHANGED).exists()
    client.patch(f"{DASH}/products/{pid}/", {"price_syp": "5500"}, format="json")
    price_log = AuditLog.objects.get(action=AuditAction.STORE_PRICE_CHANGED)
    assert price_log.old_value["price_syp"] == "5000.00" and price_log.new_value["price_syp"] == "5500.00"
    assert _data(client.get(f"{DASH}/products/?search=دفتر"))["count"] == 1
    assert client.delete(f"{DASH}/products/{pid}/").status_code == 200
    product = StoreProduct.objects.get(pk=pid)
    assert product.is_deleted and not product.is_active
    assert _data(client.get(f"{DASH}/products/"))["count"] == 0


def test_variant_create_makes_the_shelf_row_with_opening_stock_and_reorder_level(staff, product):
    client = client_for(staff)
    created = client.post(
        f"{DASH}/variants/",
        {
            "product": product.pk,
            "sku": "PEN-BLU",
            "colour_name": "أزرق",
            "colour_hex": "#0000ff",
            "size_label": "0.7",
            "price_syp": "1200",
            "initial_stock": 25,
            "reorder_level": 5,
        },
        format="json",
    )
    assert created.status_code == 201, created.content
    data = _data(created)
    assert (
        data["colour_hex"] == "#0000FF"
        and data["stock"] == 25
        and data["reorder_level"] == 5
        and data["inventory_item"]
    )
    variant = StoreVariant.objects.get(pk=data["id"])
    assert variant.shelf.reorder_level == 5 and variant.shelf.sale_price == Decimal("1200.00")
    assert "initial_stock" not in data
    duplicate = client.post(
        f"{DASH}/variants/", {"product": product.pk, "colour_name": "أزرق", "size_label": "0.7"}, format="json"
    )
    assert duplicate.status_code == 400
    sku_clash = client.post(
        f"{DASH}/variants/", {"product": product.pk, "sku": "PEN-BLU", "colour_name": "x"}, format="json"
    )
    assert sku_clash.status_code == 400
    assert (
        client.post(f"{DASH}/variants/", {"product": product.pk, "colour_hex": "blue"}, format="json").status_code
        == 400
    )
    changed = client.patch(f"{DASH}/variants/{data['id']}/", {"price_syp": "1300", "reorder_level": 8}, format="json")
    assert changed.status_code == 200 and _data(changed)["reorder_level"] == 8
    variant.shelf.refresh_from_db()
    assert variant.shelf.sale_price == Decimal("1300.00")
    assert AuditLog.objects.filter(action=AuditAction.STORE_PRICE_CHANGED, target_id=str(variant.pk)).exists()
    other = make_variant(StoreProduct.objects.create(name="o", category=product.category), 1)
    moved = client.patch(f"{DASH}/variants/{data['id']}/", {"product": other.product_id}, format="json")
    assert moved.status_code == 400
    assert client.delete(f"{DASH}/variants/{data['id']}/").status_code == 200
    variant.shelf.refresh_from_db()
    assert variant.shelf.is_sellable is False


def test_variant_of_a_deleted_product_is_refused(staff, product):
    product.is_deleted = True
    product.save()
    assert (
        client_for(staff)
        .post(f"{DASH}/variants/", {"product": product.pk, "colour_name": "x"}, format="json")
        .status_code
        == 400
    )


def test_stock_movements_use_the_inventory_log_and_audit(staff, variant):
    client = client_for(staff)
    url = f"{DASH}/variants/{variant.pk}/stock/"
    bought = client.post(url, {"movement_type": "purchase", "quantity": 15, "note": "توريد"}, format="json")
    assert bought.status_code == 201 and _data(bought)["stock"] == 25
    assert client.post(url, {"movement_type": "waste", "quantity": 5}, format="json").status_code == 201
    assert client.post(url, {"movement_type": "adjustment", "quantity": -3}, format="json").status_code == 201
    assert client.post(url, {"movement_type": "waste", "quantity": 500}, format="json").status_code == 400  # below zero
    assert client.post(url, {"movement_type": "purchase", "quantity": -1}, format="json").status_code == 400
    assert client.post(url, {"movement_type": "purchase", "quantity": 0}, format="json").status_code == 400
    assert client.post(url, {"movement_type": "usage", "quantity": 1}, format="json").status_code == 400
    variant.shelf.refresh_from_db()
    assert variant.shelf.quantity == 17
    assert StockMovement.objects.filter(item=variant.shelf, created_by=staff).count() == 3
    logged = AuditLog.objects.filter(action=AuditAction.STOCK_MOVEMENT_RECORDED, new_value__store_variant=variant.pk)
    assert logged.count() == 3


def test_image_upload_makes_a_thumbnail_and_one_primary(staff, product):
    client = client_for(staff)
    first = client.post(
        f"{DASH}/images/",
        {"product": product.pk, "image": png_upload("a.png", (1200, 900)), "alt_text": "أمامي"},
        format="multipart",
    )
    assert first.status_code == 201, first.content
    a = _data(first)
    assert a["is_primary"] is True  # the first picture becomes primary
    second = client.post(f"{DASH}/images/", {"product": product.pk, "image": png_upload("b.png")}, format="multipart")
    assert _data(second)["is_primary"] is False
    swapped = client.patch(f"{DASH}/images/{_data(second)['id']}/", {"is_primary": True}, format="json")
    assert _data(swapped)["is_primary"] is True
    rows = {row["id"]: row["is_primary"] for row in _data(client.get(f"{DASH}/images/?product={product.pk}"))}
    assert list(rows.values()).count(True) == 1 and rows[_data(second)["id"]] is True
    from PIL import Image

    from .models import StoreProductImage

    image = StoreProductImage.objects.get(pk=a["id"])
    with image.thumb.open("rb") as handle:
        thumb = Image.open(handle)
        assert thumb.format == "WEBP" and max(thumb.size) <= 480
    replaced = client.patch(f"{DASH}/images/{a['id']}/", {"image": png_upload("c.png", (50, 50))}, format="multipart")
    assert replaced.status_code == 200
    image.refresh_from_db()
    with image.thumb.open("rb") as handle:
        assert Image.open(handle).size == (50, 50)
    assert client.delete(f"{DASH}/images/{a['id']}/").status_code == 200


def test_image_validation(staff, product):
    from django.core.files.uploadedfile import SimpleUploadedFile

    client = client_for(staff)
    fake = SimpleUploadedFile("a.png", b"GIF89a-not-a-png", content_type="image/png")
    assert client.post(f"{DASH}/images/", {"product": product.pk, "image": fake}, format="multipart").status_code == 400
    other = StoreProduct.objects.create(name="o", category=product.category)
    foreign = make_variant(other, 1)
    mismatch = client.post(
        f"{DASH}/images/", {"product": product.pk, "variant": foreign.pk, "image": png_upload()}, format="multipart"
    )
    assert mismatch.status_code == 400


# ---- orders -----------------------------------------------------------------------------------------------------------------------


def test_order_list_filters_and_detail(staff, student, other_student, variant):
    first = _order(student, variant)
    second = _order(other_student, variant, 1)
    client = client_for(staff)
    listing = _data(client.get(f"{DASH}/orders/"))
    assert listing["count"] == 2 and listing["results"][0]["id"] == second and listing["results"][0]["items_count"] == 1
    assert listing["results"][0]["customer"]["full_name"].startswith("Store")
    assert _data(client.get(f"{DASH}/orders/?status=cancelled"))["count"] == 0
    assert _data(client.get(f"{DASH}/orders/?q=ST-{first:06d}"))["count"] == 1
    assert _data(client.get(f"{DASH}/orders/?q={first}"))["count"] == 1
    assert _data(client.get(f"{DASH}/orders/?q=student 2"))["count"] == 1
    assert _data(client.get(f"{DASH}/orders/?q=0000"))["count"] == 2  # phone numbers contain it
    assert _data(client.get(f"{DASH}/orders/?q=ST-999999"))["count"] == 0
    assert _data(client.get(f"{DASH}/orders/?from=2999-01-01"))["count"] == 0
    assert _data(client.get(f"{DASH}/orders/?to=2000-01-01"))["count"] == 0
    assert _data(client.get(f"{DASH}/orders/?from=garbage"))["count"] == 2
    detail = _data(client.get(f"{DASH}/orders/{first}/"))
    assert detail["items"][0]["quantity"] == 2 and detail["customer"]["id"] == student.pk and detail["history"]


def test_status_flow_notifies_the_student_and_audits(staff, student, variant):
    order_id = _order(student, variant)
    client = client_for(staff)
    Notification.objects.all().delete()
    confirmed = _move(client, order_id, "confirmed", note="بدّلنا الدفعة")
    assert (
        confirmed.status_code == 200
        and _data(confirmed)["status"] == "confirmed"
        and _data(confirmed)["internal_notes"]
    )
    assert _data(_move(client, order_id, "ready_for_pickup"))["ready_at"]
    done = _move(client, order_id, "picked_up")
    assert _data(done)["picked_up_at"] and _data(done)["status"] == "picked_up"
    texts = list(Notification.objects.filter(user=student).order_by("id").values_list("body", flat=True))
    assert len(texts) == 3 and "جاهز للاستلام" in texts[1] and "تم تسليم" in texts[2]
    assert Notification.objects.filter(user=student, data__status="picked_up", data__kind="store_order_status").exists()
    logs = AuditLog.objects.filter(action=AuditAction.STORE_ORDER_STATUS_CHANGED, actor=staff).order_by("id")
    assert [log.new_value["status"] for log in logs] == ["confirmed", "ready_for_pickup", "picked_up"]
    assert [row["new_status"] for row in _data(client.get(f"{DASH}/orders/{order_id}/"))["history"]] == [
        "pending",
        "confirmed",
        "ready_for_pickup",
        "picked_up",
    ]
    student_view = _data(client_for(student).get(f"{API}/orders/{order_id}/"))
    assert student_view["status"] == "picked_up" and "internal_notes" not in student_view


def test_invalid_transitions_are_refused(staff, student, variant):
    order_id = _order(student, variant)
    client = client_for(staff)
    for target in ("ready_for_pickup", "picked_up"):
        refused = _move(client, order_id, target)
        assert refused.status_code == 409 and refused.json()["code"] == "INVALID_TRANSITION"
    assert _move(client, order_id, "pending").status_code == 409  # nothing moves back to pending
    _move(client, order_id, "confirmed")
    assert _move(client, order_id, "confirmed").status_code == 409
    _move(client, order_id, "ready_for_pickup")
    _move(client, order_id, "picked_up")
    assert _move(client, order_id, "cancelled", reason="x").status_code == 409  # delivered orders are final
    assert client.patch(f"{DASH}/orders/999999/status/", {"status": "confirmed"}, format="json").status_code == 404


def test_staff_cancel_needs_a_reason_returns_stock_and_tells_the_student(staff, student, variant):
    order_id = _order(student, variant, 3)
    client = client_for(staff)
    _move(client, order_id, "confirmed")
    assert _move(client, order_id, "cancelled").status_code == 400
    Notification.objects.all().delete()
    cancelled = _move(client, order_id, "cancelled", reason="نفد المنتج")
    assert cancelled.status_code == 200
    data = _data(cancelled)
    assert data["cancel_reason"] == "نفد المنتج" and data["cancelled_by_customer"] is False
    variant.shelf.refresh_from_db()
    assert variant.shelf.quantity == 10
    assert "نفد المنتج" in Notification.objects.get(user=student).body
    assert _move(client, order_id, "cancelled", reason="again").status_code == 409
    variant.shelf.refresh_from_db()
    assert variant.shelf.quantity == 10
    log = AuditLog.objects.filter(action=AuditAction.STORE_ORDER_STATUS_CHANGED, new_value__status="cancelled").get()
    assert log.new_value["reason"] == "نفد المنتج" and log.old_value == {"status": "confirmed"}


def test_a_ready_order_can_still_be_cancelled(staff, student, variant):
    order_id = _order(student, variant)
    client = client_for(staff)
    _move(client, order_id, "confirmed")
    _move(client, order_id, "ready_for_pickup")
    assert _move(client, order_id, "cancelled", reason="لم يحضر").status_code == 200
    assert StoreOrder.objects.get(pk=order_id).status == StoreOrderStatus.CANCELLED


# ---- reviews ----------------------------------------------------------------------------------------------------------------------


def _review(student, product, variant, rating):
    order = StoreOrder.objects.create(
        user=student, status=StoreOrderStatus.PICKED_UP, currency="SYP", subtotal=1, total=1
    )
    order.items.create(product=product, variant=variant, name="x", unit_price=1, quantity=1, line_total=1)
    return StoreReview.objects.create(user=student, product=product, rating=rating, text="نص")


def test_moderation_hides_updates_the_rating_audits_and_can_be_undone(staff, student, other_student, product, variant):
    keep = _review(student, product, variant, 5)
    bad = _review(other_student, product, variant, 1)
    from . import services

    services.recompute_rating(product.pk)
    product.refresh_from_db()
    assert product.rating_count == 2 and product.rating_avg == Decimal("3.00")
    client = client_for(staff)
    assert client.post(f"{DASH}/reviews/{bad.pk}/hide/", {}, format="json").status_code == 400  # a reason is required
    hidden = client.post(f"{DASH}/reviews/{bad.pk}/hide/", {"reason": "إساءة"}, format="json")
    assert hidden.status_code == 200 and _data(hidden)["is_hidden"] is True
    product.refresh_from_db()
    assert product.rating_count == 1 and product.rating_avg == Decimal("5.00")
    public = _data(client_for(student).get(f"{API}/products/{product.pk}/reviews/"))
    assert [row["id"] for row in public["results"]] == [keep.pk]
    assert _data(client.get(f"{DASH}/reviews/?is_hidden=true"))["count"] == 1
    assert _data(client.get(f"{DASH}/reviews/?product={product.pk}"))["count"] == 2
    shown = client.post(f"{DASH}/reviews/{bad.pk}/unhide/", {}, format="json")
    assert shown.status_code == 200 and _data(shown)["is_hidden"] is False and _data(shown)["hidden_reason"] == ""
    product.refresh_from_db()
    assert product.rating_count == 2
    log = AuditLog.objects.filter(action=AuditAction.STORE_REVIEW_MODERATED).order_by("id")
    assert [entry.new_value["hidden"] for entry in log] == [True, False] and log[0].new_value["reason"] == "إساءة"
    assert client.post(f"{DASH}/reviews/999999/hide/", {"reason": "x"}, format="json").status_code == 404


def test_reviews_are_not_writable_through_the_listing(staff, student, product, variant):
    review = _review(student, product, variant, 4)
    assert client_for(staff).delete(f"{DASH}/reviews/{review.pk}/").status_code == 405
    assert client_for(staff).patch(f"{DASH}/reviews/{review.pk}/", {"rating": 1}, format="json").status_code == 405


# ---- summary ----------------------------------------------------------------------------------------------------------------------


def test_summary_counts_and_low_stock(staff, student, product, variant):
    _order(student, variant)
    empty = make_variant(product, stock=0, colour_name="فارغ")
    low = make_variant(product, stock=3, colour_name="قليل")
    low.shelf.reorder_level = 5
    low.shelf.save()
    summary = _data(client_for(staff).get(f"{DASH}/summary/"))
    assert summary["orders_by_status"] == {
        "pending": 1,
        "confirmed": 0,
        "ready_for_pickup": 0,
        "picked_up": 0,
        "cancelled": 0,
    }
    assert summary["orders_today"] == 1 and summary["products_active"] == 1 and summary["reviews_hidden"] == 0
    assert [row["variant"] for row in summary["low_stock"]] == [empty.pk, low.pk]
    assert summary["low_stock"][1] == {
        "variant": low.pk,
        "product": product.name,
        "label": "قليل",
        "available": 3,
        "reorder_level": 5,
    }


def test_quick_access_counts_new_store_orders(staff, student, variant):
    _order(student, variant)
    counts = _data(client_for(staff).get("/api/v1/dashboard/quick-access/"))["counts"]
    assert counts["store_orders_new"] == 1


def test_seed_command_is_idempotent_and_creates_no_products(db):
    from django.core.management import call_command

    call_command("seed_store_defaults")
    call_command("seed_store_defaults")
    assert StoreCategory.objects.count() == 6 and StoreBrand.objects.count() == 8
    assert StoreProduct.objects.count() == 0 and StoreVariant.objects.count() == 0
    StoreCategory.objects.filter(slug="pens").update(name="غيرتها")
    call_command("seed_store_defaults")
    assert StoreCategory.objects.get(slug="pens").name == "غيرتها"  # staff edits are never overwritten
