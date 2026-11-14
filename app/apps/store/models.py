"""The stationery store: a catalogue students browse, a server-side cart and in-store-pickup orders.

Stock is NOT kept here. Every variant points at a ``finance.InventoryItem`` (the same shelf the counter's quick-sale
screen and the low-stock alerts use), so a quantity only ever changes through ``StockMovement`` rows.
There is no online payment: the student pays in cash when picking the order up.
"""

from __future__ import annotations

import re
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.common.models import BaseModel
from apps.finance.models import InventoryItem

CURRENCIES = ("SYP", "USD")
_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def money_field(**kwargs):
    return models.DecimalField(max_digits=12, decimal_places=2, **kwargs)


def validate_hex_colour(value: str) -> None:
    if value and not _HEX_RE.match(value):
        raise ValidationError("Use a colour like #1A2B3C.")


class StoreBrand(BaseModel):
    name = models.CharField(max_length=100)
    logo = models.ImageField(upload_to="store/brands/", null=True, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["name"], condition=models.Q(is_deleted=False), name="store_brand_unique_live_name"
            )
        ]

    def __str__(self) -> str:
        return self.name


class StoreCategory(BaseModel):
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=60, allow_unicode=False)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "store categories"
        constraints = [
            models.UniqueConstraint(
                fields=["slug"], condition=models.Q(is_deleted=False), name="store_category_unique_live_slug"
            )
        ]

    def __str__(self) -> str:
        return self.name


class StoreProduct(BaseModel):
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    brand = models.ForeignKey(StoreBrand, on_delete=models.PROTECT, related_name="products", null=True, blank=True)
    category = models.ForeignKey(StoreCategory, on_delete=models.PROTECT, related_name="products")
    specs = models.JSONField(default=list, blank=True, help_text='List of {"label": "...", "value": "..."}.')
    # Base price per currency; ``None`` means "not sold in this currency". A variant may override either.
    price_syp = money_field(null=True, blank=True, validators=[MinValueValidator(Decimal("0.01"))])
    price_usd = money_field(null=True, blank=True, validators=[MinValueValidator(Decimal("0.01"))])
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    # Maintained by ``services.recompute_rating`` (visible reviews only) and when an order is picked up.
    rating_avg = models.DecimalField(max_digits=3, decimal_places=2, default=Decimal("0"))
    rating_count = models.PositiveIntegerField(default=0)
    sold_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "-created_at"]
        indexes = [
            models.Index(fields=["category", "is_active"], name="store_product_category_idx"),
            models.Index(fields=["is_active", "-sold_count"], name="store_product_popular_idx"),
        ]

    def __str__(self) -> str:
        return self.name


class StoreVariant(BaseModel):
    """One sellable colour/size of a product. Its stock is the linked inventory item's quantity."""

    product = models.ForeignKey(StoreProduct, on_delete=models.CASCADE, related_name="variants")
    sku = models.CharField(max_length=64, blank=True)
    colour_name = models.CharField(max_length=50, blank=True)
    colour_hex = models.CharField(max_length=7, blank=True, validators=[validate_hex_colour])
    size_label = models.CharField(max_length=50, blank=True)
    price_syp = money_field(null=True, blank=True, validators=[MinValueValidator(Decimal("0.01"))])
    price_usd = money_field(null=True, blank=True, validators=[MinValueValidator(Decimal("0.01"))])
    inventory_item = models.OneToOneField(
        "finance.InventoryItem", on_delete=models.PROTECT, related_name="store_variant", null=True, blank=True
    )
    max_per_order = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1)], help_text="Per-order cap; empty = the store default."
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["sku"],
                condition=models.Q(is_deleted=False) & ~models.Q(sku=""),
                name="store_variant_unique_live_sku",
            ),
            models.UniqueConstraint(
                fields=["product", "colour_name", "size_label"],
                condition=models.Q(is_deleted=False),
                name="store_variant_unique_live_options",
            ),
        ]

    @property
    def label(self) -> str:
        return " / ".join(part for part in (self.colour_name, self.size_label) if part)

    def price_for(self, currency: str) -> Decimal | None:
        """The unit price in ``currency`` (the variant's own, else the product's), or None when not sold in it."""

        if currency not in CURRENCIES:
            return None
        field = f"price_{currency.lower()}"
        own = getattr(self, field)
        return own if own is not None else getattr(self.product, field)

    @property
    def shelf(self) -> InventoryItem:
        """The inventory row holding this variant's stock; every variant made through the API has one."""

        if self.inventory_item is None:
            raise ValueError("This variant has no stock row.")
        return self.inventory_item

    @property
    def available(self) -> int:
        """Whole units on the shelf right now (0 when the variant has no stock row)."""

        item = self.inventory_item
        if item is None or not item.is_active or item.is_deleted:
            return 0
        return max(int(item.quantity), 0)

    def __str__(self) -> str:
        return f"{self.product.name} {self.label}".strip()


class StoreProductImage(BaseModel):
    product = models.ForeignKey(StoreProduct, on_delete=models.CASCADE, related_name="images")
    variant = models.ForeignKey(StoreVariant, on_delete=models.SET_NULL, related_name="images", null=True, blank=True)
    image = models.ImageField(upload_to="store/products/")
    thumb = models.FileField(upload_to="store/thumbs/", null=True, blank=True, editable=False)
    alt_text = models.CharField(max_length=150, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_primary = models.BooleanField(default=False)

    class Meta:
        ordering = ["-is_primary", "sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["product"],
                condition=models.Q(is_primary=True) & models.Q(is_deleted=False),
                name="store_image_one_primary_per_product",
            )
        ]


class StoreReview(BaseModel):
    product = models.ForeignKey(StoreProduct, on_delete=models.CASCADE, related_name="reviews")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="store_reviews")
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    text = models.CharField(max_length=1000, blank=True)
    edit_count = models.PositiveSmallIntegerField(default=0)
    is_hidden = models.BooleanField(default=False)
    hidden_reason = models.CharField(max_length=255, blank=True)
    moderated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    moderated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "product"], condition=models.Q(is_deleted=False), name="store_review_one_per_user"
            ),
            models.CheckConstraint(
                condition=models.Q(rating__gte=1) & models.Q(rating__lte=5), name="store_review_rating_1_5"
            ),
        ]


class StoreCart(BaseModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="store_cart")


class StoreCartItem(BaseModel):
    cart = models.ForeignKey(StoreCart, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(StoreVariant, on_delete=models.CASCADE, related_name="cart_items")
    quantity = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["cart", "variant"], name="store_cart_one_line_per_variant"),
            models.CheckConstraint(condition=models.Q(quantity__gte=1), name="store_cart_quantity_positive"),
        ]


class StoreOrderStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    CONFIRMED = "confirmed", "Confirmed"
    READY = "ready_for_pickup", "Ready for pickup"
    PICKED_UP = "picked_up", "Picked up"
    CANCELLED = "cancelled", "Cancelled"


#: status -> where staff may move it. Terminal states have no way out.
STORE_TRANSITIONS: dict[str, set[str]] = {
    StoreOrderStatus.PENDING: {StoreOrderStatus.CONFIRMED, StoreOrderStatus.CANCELLED},
    StoreOrderStatus.CONFIRMED: {StoreOrderStatus.READY, StoreOrderStatus.CANCELLED},
    StoreOrderStatus.READY: {StoreOrderStatus.PICKED_UP, StoreOrderStatus.CANCELLED},
    StoreOrderStatus.PICKED_UP: set(),
    StoreOrderStatus.CANCELLED: set(),
}


class StoreOrder(BaseModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="store_orders")
    status = models.CharField(max_length=24, choices=StoreOrderStatus.choices, default=StoreOrderStatus.PENDING)
    currency = models.CharField(max_length=3)
    subtotal = money_field()
    rounding_adjustment = money_field(default=Decimal("0"))
    total = money_field()
    notes = models.CharField(max_length=500, blank=True)
    internal_notes = models.CharField(max_length=500, blank=True)
    cancel_reason = models.CharField(max_length=255, blank=True)
    cancelled_by_customer = models.BooleanField(default=False)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    picked_up_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    # Set when the reserved stock went back to the shelf (cancel), so it can never be returned twice.
    stock_released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["user", "status"], name="store_order_user_status_idx"),
            models.Index(fields=["status", "created_at"], name="store_order_status_idx"),
        ]

    @property
    def code(self) -> str:
        return f"ST-{self.pk:06d}"


class StoreOrderItem(BaseModel):
    order = models.ForeignKey(StoreOrder, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(StoreProduct, on_delete=models.PROTECT, related_name="order_items")
    variant = models.ForeignKey(StoreVariant, on_delete=models.PROTECT, related_name="order_items")
    # Snapshots: what the student bought stays readable when the catalogue changes.
    name = models.CharField(max_length=150)
    brand_name = models.CharField(max_length=100, blank=True)
    variant_label = models.CharField(max_length=110, blank=True)
    sku = models.CharField(max_length=64, blank=True)
    unit_price = money_field()
    quantity = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    line_total = money_field()

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["product"], name="store_orderitem_product_idx")]


class StoreOrderStatusHistory(BaseModel):
    order = models.ForeignKey(StoreOrder, on_delete=models.CASCADE, related_name="history")
    old_status = models.CharField(max_length=24, blank=True)
    new_status = models.CharField(max_length=24)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["created_at", "id"]
