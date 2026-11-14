"""Model constraints, prices in two currencies, SYP rounding and the shelf (stock) link."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from apps.finance.models import InventoryItem, StockMovement, StockMovementType

from . import services
from .models import (
    StoreBrand,
    StoreCart,
    StoreCartItem,
    StoreCategory,
    StoreProductImage,
    StoreReview,
    StoreVariant,
)
from .test_support import make_variant, png_upload

pytestmark = pytest.mark.django_db


def test_live_brand_name_and_category_slug_are_unique(brand, category):
    with pytest.raises(IntegrityError), transaction.atomic():
        StoreBrand.objects.create(name="Pilot")
    with pytest.raises(IntegrityError), transaction.atomic():
        StoreCategory.objects.create(name="x", slug="pens")
    brand.is_deleted = True
    brand.save()
    assert StoreBrand.objects.create(name="Pilot").pk  # a deleted row frees the name


def test_variant_sku_and_options_are_unique(product, variant):
    with pytest.raises(IntegrityError), transaction.atomic():
        StoreVariant.objects.create(product=product, sku="PEN-RED", colour_name="أزرق")
    with pytest.raises(IntegrityError), transaction.atomic():
        StoreVariant.objects.create(product=product, colour_name="أحمر")
    assert StoreVariant.objects.create(product=product, colour_name="أزرق").pk
    assert StoreVariant.objects.create(product=product, colour_name="أخضر").pk  # blank SKUs never clash


def test_one_primary_image_per_product(product):
    StoreProductImage.objects.create(product=product, image="a.png", is_primary=True)
    with pytest.raises(IntegrityError), transaction.atomic():
        StoreProductImage.objects.create(product=product, image="b.png", is_primary=True)


def test_one_review_per_user_and_rating_bounds(product, student):
    StoreReview.objects.create(product=product, user=student, rating=5)
    with pytest.raises(IntegrityError), transaction.atomic():
        StoreReview.objects.create(product=product, user=student, rating=4)


def test_rating_check_constraint(product, student):
    with pytest.raises(IntegrityError), transaction.atomic():
        StoreReview.objects.create(product=product, user=student, rating=6)


def test_cart_has_one_line_per_variant_and_positive_quantity(student, variant):
    cart = StoreCart.objects.create(user=student)
    StoreCartItem.objects.create(cart=cart, variant=variant, quantity=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        StoreCartItem.objects.create(cart=cart, variant=variant, quantity=2)


def test_price_falls_back_to_the_product_and_variant_overrides(product):
    plain = make_variant(product, colour_name="أزرق")
    special = make_variant(product, colour_name="ذهبي", price_syp=Decimal("5000"), price_usd=None)
    assert plain.price_for("SYP") == Decimal("1003.00") and plain.price_for("USD") == Decimal("2.50")
    assert special.price_for("SYP") == Decimal("5000") and special.price_for("USD") == Decimal("2.50")
    assert plain.price_for("EUR") is None
    product.price_usd = None
    product.save()
    plain.refresh_from_db()
    plain.product.refresh_from_db()
    assert plain.price_for("USD") is None


def test_syp_total_is_rounded_to_tens_and_usd_keeps_cents(product, variant):
    syp = services.price_lines([(variant, 1)], "SYP")
    assert syp["subtotal"] == Decimal("1003.00")
    assert syp["total"] == Decimal("1010.00")  # last digit 3 rounds up
    assert syp["rounding_adjustment"] == Decimal("7.00")
    usd = services.price_lines([(variant, 3)], "USD")
    assert usd["total"] == Decimal("7.50") and usd["rounding_adjustment"] == Decimal("0.00")
    low = make_variant(product, colour_name="رخيص", price_syp=Decimal("1.00"))
    assert services.price_lines([(low, 1)], "SYP")["total"] == Decimal("10.00")  # a price never rounds to zero


def test_a_currency_without_a_price_is_refused_not_guessed(product):
    only_syp = make_variant(product, colour_name="سيرياني", price_usd=None)
    product.price_usd = None
    product.save()
    only_syp.refresh_from_db()
    with pytest.raises(services.StoreError) as excinfo:
        services.price_lines([(only_syp, 1)], "USD")
    assert excinfo.value.api_code == "PRICE_UNAVAILABLE"


def test_variant_stock_is_the_inventory_item_quantity(variant):
    assert variant.available == 10
    item = variant.shelf
    assert item.is_sellable and item.sale_price == Decimal("1003.00") and item.currency == "SYP"
    assert StockMovement.objects.filter(item=item, movement_type=StockMovementType.PURCHASE, quantity=10).exists()
    item.is_active = False
    item.save()
    variant.refresh_from_db()
    assert variant.available == 0
    assert StoreVariant(product=variant.product).available == 0  # no shelf row, nothing to sell


def test_shelf_names_never_collide(product):
    first = make_variant(product, colour_name="")
    second = make_variant(product, colour_name="", size_label="كبير")
    assert first.shelf.name != second.shelf.name
    assert InventoryItem.objects.filter(is_sellable=True).count() == 2


def test_sync_updates_counter_price_and_hides_deleted_variants(variant):
    variant.price_syp = Decimal("2000")
    variant.save()
    services.sync_inventory_item(variant)
    assert variant.shelf.sale_price == Decimal("2000.00")
    variant.is_deleted = True
    services.sync_inventory_item(variant)
    variant.shelf.refresh_from_db()
    assert variant.shelf.is_sellable is False


def test_colour_hex_validation(variant):
    from django.core.exceptions import ValidationError

    variant.colour_hex = "red"
    with pytest.raises(ValidationError):
        variant.full_clean()
    variant.colour_hex = "#A1b2C3"
    variant.full_clean()


def test_png_upload_helper_is_a_real_image():
    assert png_upload().read()[1:4] == b"PNG"
