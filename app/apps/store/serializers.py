from __future__ import annotations

from django.conf import settings
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.common.file_validation import validate_image_upload

from .models import (
    CURRENCIES,
    StoreBrand,
    StoreCategory,
    StoreOrder,
    StoreOrderItem,
    StoreOrderStatusHistory,
    StoreProduct,
    StoreProductImage,
    StoreReview,
    StoreVariant,
)
from .services import ensure_inventory_item, line_cap, sync_inventory_item

PRICE_MAP = {"type": "object", "additionalProperties": {"type": "string", "nullable": True}}


# ---- authenticated image URLs (production does not publish /media/) ---------------------------------------------------------------


def _absolute(path: str, request) -> str:
    return request.build_absolute_uri(path) if request else path


def image_url(image: StoreProductImage, request, *, thumb: bool = False) -> str:
    path = f"/api/v1/store/images/{image.pk}/?v={int(image.updated_at.timestamp())}"
    return _absolute(path + ("&variant=thumb" if thumb else ""), request)


def logo_url(brand: StoreBrand, request) -> str | None:
    if not brand.logo:
        return None
    return _absolute(f"/api/v1/store/brands/{brand.pk}/logo/?v={int(brand.updated_at.timestamp())}", request)


def price_map(obj) -> dict:
    return {
        currency: (
            None
            if getattr(obj, f"price_{currency.lower()}") is None
            else str(getattr(obj, f"price_{currency.lower()}"))
        )
        for currency in CURRENCIES
    }


def validate_specs(value):
    if not isinstance(value, list) or len(value) > 20:
        raise serializers.ValidationError("Send a list of at most 20 {label, value} pairs.")
    clean = []
    for row in value:
        if not isinstance(row, dict) or set(row) - {"label", "value"}:
            raise serializers.ValidationError("Each spec is {label, value}.")
        label, text = str(row.get("label", "")).strip(), str(row.get("value", "")).strip()
        if not label or not text or len(label) > 60 or len(text) > 200:
            raise serializers.ValidationError("A spec needs a label (60 chars) and a value (200 chars).")
        clean.append({"label": label, "value": text})
    return clean


# ---- student: catalogue -----------------------------------------------------------------------------------------------------------


class BrandSerializer(serializers.ModelSerializer):
    logo_url = serializers.SerializerMethodField()

    class Meta:
        model = StoreBrand
        fields = ["id", "name", "logo_url"]

    def get_logo_url(self, obj) -> str | None:
        return logo_url(obj, self.context.get("request"))


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = StoreCategory
        fields = ["id", "name", "slug"]


class BrandRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = StoreBrand
        fields = ["id", "name"]


def _primary_image(product: StoreProduct) -> StoreProductImage | None:
    images = [img for img in product.images.all() if not img.is_deleted]
    return images[0] if images else None  # Meta.ordering puts the primary first


class ProductListSerializer(serializers.ModelSerializer):
    """Card of the product grid: picture, name, rating and the lowest price per currency."""

    brand = BrandRefSerializer(read_only=True)
    category = CategorySerializer(read_only=True)
    image_url = serializers.SerializerMethodField()
    thumb_url = serializers.SerializerMethodField()
    price_from = serializers.SerializerMethodField()
    in_stock = serializers.SerializerMethodField()
    rating_avg = serializers.DecimalField(max_digits=3, decimal_places=2, read_only=True)

    class Meta:
        model = StoreProduct
        fields = [
            "id",
            "name",
            "brand",
            "category",
            "image_url",
            "thumb_url",
            "rating_avg",
            "rating_count",
            "price_from",
            "in_stock",
        ]

    def get_image_url(self, obj) -> str | None:
        image = _primary_image(obj)
        return image_url(image, self.context.get("request")) if image else None

    def get_thumb_url(self, obj) -> str | None:
        image = _primary_image(obj)
        return image_url(image, self.context.get("request"), thumb=True) if image else None

    @extend_schema_field(PRICE_MAP)
    def get_price_from(self, obj) -> dict:
        variants = [v for v in obj.variants.all() if v.is_active and not v.is_deleted]
        out: dict[str, str | None] = {}
        for currency in CURRENCIES:
            prices = [p for p in (v.price_for(currency) for v in variants) if p is not None]
            out[currency] = str(min(prices)) if prices else None
        return out

    def get_in_stock(self, obj) -> bool:
        return any(v.is_active and not v.is_deleted and v.available > 0 for v in obj.variants.all())


class VariantSerializer(serializers.ModelSerializer):
    prices = serializers.SerializerMethodField()
    available = serializers.SerializerMethodField()
    in_stock = serializers.SerializerMethodField()
    max_per_order = serializers.SerializerMethodField()
    image_id = serializers.SerializerMethodField()

    class Meta:
        model = StoreVariant
        fields = [
            "id",
            "sku",
            "colour_name",
            "colour_hex",
            "size_label",
            "prices",
            "available",
            "in_stock",
            "max_per_order",
            "image_id",
        ]

    @extend_schema_field(PRICE_MAP)
    def get_prices(self, obj) -> dict:
        return {c: (None if (p := obj.price_for(c)) is None else str(p)) for c in CURRENCIES}

    def get_available(self, obj) -> int:
        return obj.available

    def get_in_stock(self, obj) -> bool:
        return obj.available > 0

    def get_max_per_order(self, obj) -> int:
        return line_cap(obj)

    def get_image_id(self, obj) -> int | None:
        images = [img for img in obj.images.all() if not img.is_deleted]
        return images[0].pk if images else None


class ProductImageSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()
    thumb_url = serializers.SerializerMethodField()

    class Meta:
        model = StoreProductImage
        fields = ["id", "url", "thumb_url", "alt_text", "is_primary", "variant"]

    def get_url(self, obj) -> str:
        return image_url(obj, self.context.get("request"))

    def get_thumb_url(self, obj) -> str:
        return image_url(obj, self.context.get("request"), thumb=True)


class ProductDetailSerializer(ProductListSerializer):
    variants = serializers.SerializerMethodField()
    images = serializers.SerializerMethodField()
    prices = serializers.SerializerMethodField()
    can_review = serializers.SerializerMethodField()

    class Meta(ProductListSerializer.Meta):
        fields = [
            *ProductListSerializer.Meta.fields,
            "description",
            "specs",
            "prices",
            "variants",
            "images",
            "can_review",
        ]

    @extend_schema_field(VariantSerializer(many=True))
    def get_variants(self, obj):
        rows = [v for v in obj.variants.all() if v.is_active and not v.is_deleted]
        return VariantSerializer(rows, many=True, context=self.context).data

    @extend_schema_field(ProductImageSerializer(many=True))
    def get_images(self, obj):
        rows = [img for img in obj.images.all() if not img.is_deleted]
        return ProductImageSerializer(rows, many=True, context=self.context).data

    @extend_schema_field(PRICE_MAP)
    def get_prices(self, obj) -> dict:
        return price_map(obj)

    def get_can_review(self, obj) -> bool:
        from .services import has_received

        request = self.context.get("request")
        return bool(request and request.user.is_authenticated and has_received(request.user, obj.pk))


class ReviewSerializer(serializers.ModelSerializer):
    author = serializers.SerializerMethodField()
    edited = serializers.SerializerMethodField()

    class Meta:
        model = StoreReview
        fields = ["id", "rating", "text", "author", "edited", "created_at", "updated_at"]

    def get_author(self, obj) -> str:
        """First name only: reviews are public to every logged-in user."""

        return (obj.user.full_name or "").split(" ")[0] or "طالب"

    def get_edited(self, obj) -> bool:
        return obj.edit_count > 0


class ReviewWriteSerializer(serializers.Serializer):
    rating = serializers.IntegerField(min_value=1, max_value=5)
    text = serializers.CharField(max_length=1000, required=False, allow_blank=True, default="")


class ReviewEditSerializer(serializers.Serializer):
    rating = serializers.IntegerField(min_value=1, max_value=5, required=False)
    text = serializers.CharField(max_length=1000, required=False, allow_blank=True)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Send a rating or a text.")
        return attrs


# ---- student: cart and orders -----------------------------------------------------------------------------------------------------


class CartAddSerializer(serializers.Serializer):
    variant = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=1, max_value=99, default=1)


class CartQuantitySerializer(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=1, max_value=99)


class CheckoutSerializer(serializers.Serializer):
    currency = serializers.ChoiceField(choices=list(CURRENCIES))
    notes = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")


class CancelSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class OrderItemSerializer(serializers.ModelSerializer):
    thumb_url = serializers.SerializerMethodField()

    class Meta:
        model = StoreOrderItem
        fields = [
            "id",
            "product",
            "variant",
            "name",
            "brand_name",
            "variant_label",
            "sku",
            "unit_price",
            "quantity",
            "line_total",
            "thumb_url",
        ]

    def get_thumb_url(self, obj) -> str | None:
        image = _primary_image(obj.product)
        return image_url(image, self.context.get("request"), thumb=True) if image else None


class HistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = StoreOrderStatusHistory
        fields = ["old_status", "new_status", "note", "created_at"]


class OrderListSerializer(serializers.ModelSerializer):
    code = serializers.CharField(read_only=True)
    items_count = serializers.SerializerMethodField()

    class Meta:
        model = StoreOrder
        fields = ["id", "code", "status", "currency", "total", "items_count", "created_at"]

    def get_items_count(self, obj) -> int:
        return sum(item.quantity for item in obj.items.all())


class OrderSerializer(OrderListSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    history = serializers.SerializerMethodField()
    can_cancel = serializers.SerializerMethodField()
    payment = serializers.SerializerMethodField()

    class Meta(OrderListSerializer.Meta):
        fields = [
            *OrderListSerializer.Meta.fields,
            "subtotal",
            "rounding_adjustment",
            "notes",
            "cancel_reason",
            "cancelled_by_customer",
            "confirmed_at",
            "ready_at",
            "picked_up_at",
            "cancelled_at",
            "can_cancel",
            "payment",
            "items",
            "history",
        ]

    @extend_schema_field(HistorySerializer(many=True))
    def get_history(self, obj):
        return HistorySerializer(obj.history.all(), many=True).data

    def get_can_cancel(self, obj) -> bool:
        return obj.status == "pending"

    def get_payment(self, obj) -> str:
        return "cash_on_pickup"


class CartItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    variant = serializers.SerializerMethodField()
    product = serializers.SerializerMethodField()
    quantity = serializers.IntegerField()
    prices = serializers.SerializerMethodField()
    orderable = serializers.SerializerMethodField()
    available = serializers.SerializerMethodField()
    thumb_url = serializers.SerializerMethodField()

    def get_variant(self, obj) -> dict:
        v = obj.variant
        return {
            "id": v.pk,
            "sku": v.sku,
            "colour_name": v.colour_name,
            "colour_hex": v.colour_hex,
            "size_label": v.size_label,
        }

    def get_product(self, obj) -> dict:
        p = obj.variant.product
        return {"id": p.pk, "name": p.name, "brand": p.brand.name if p.brand else ""}

    @extend_schema_field(PRICE_MAP)
    def get_prices(self, obj) -> dict:
        return {c: (None if (p := obj.variant.price_for(c)) is None else str(p)) for c in CURRENCIES}

    def get_orderable(self, obj) -> bool:
        from .services import is_orderable

        return is_orderable(obj.variant) and obj.variant.available >= obj.quantity

    def get_available(self, obj) -> int:
        return obj.variant.available

    def get_thumb_url(self, obj) -> str | None:
        image = _primary_image(obj.variant.product)
        return image_url(image, self.context.get("request"), thumb=True) if image else None


# ---- dashboard --------------------------------------------------------------------------------------------------------------------


class DashboardBrandSerializer(serializers.ModelSerializer):
    logo_url = serializers.SerializerMethodField()
    products_count = serializers.SerializerMethodField()

    class Meta:
        model = StoreBrand
        fields = ["id", "name", "logo", "logo_url", "sort_order", "is_active", "products_count", "created_at"]
        extra_kwargs = {"logo": {"write_only": True, "required": False, "allow_null": True}}

    def get_logo_url(self, obj) -> str | None:
        return logo_url(obj, self.context.get("request"))

    def get_products_count(self, obj) -> int:
        return obj.products.filter(is_deleted=False).count()

    def validate_name(self, value):
        value = value.strip()
        clash = StoreBrand.objects.filter(name__iexact=value, is_deleted=False)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if not value or clash.exists():
            raise serializers.ValidationError("A brand with this name already exists.")
        return value

    def validate_logo(self, value):
        return _checked_image(value, "logo")


class DashboardCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = StoreCategory
        fields = ["id", "name", "slug", "sort_order", "is_active"]

    def validate_slug(self, value):
        clash = StoreCategory.objects.filter(slug=value, is_deleted=False)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("This slug is already used.")
        return value


class DashboardProductSerializer(serializers.ModelSerializer):
    variants_count = serializers.SerializerMethodField()
    rating_avg = serializers.DecimalField(max_digits=3, decimal_places=2, read_only=True)

    class Meta:
        model = StoreProduct
        fields = [
            "id",
            "name",
            "description",
            "brand",
            "category",
            "specs",
            "price_syp",
            "price_usd",
            "is_active",
            "sort_order",
            "rating_avg",
            "rating_count",
            "sold_count",
            "variants_count",
            "created_at",
        ]
        read_only_fields = ["rating_count", "sold_count"]

    def get_variants_count(self, obj) -> int:
        return obj.variants.filter(is_deleted=False).count()

    def validate_specs(self, value):
        return validate_specs(value)

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("The name is required.")
        return value

    def validate_brand(self, value):
        if value is not None and value.is_deleted:
            raise serializers.ValidationError("This brand was deleted.")
        return value

    def validate_category(self, value):
        if value.is_deleted:
            raise serializers.ValidationError("This category was deleted.")
        return value


class DashboardVariantSerializer(serializers.ModelSerializer):
    initial_stock = serializers.IntegerField(write_only=True, required=False, min_value=0, max_value=100000)
    stock = serializers.SerializerMethodField()
    reorder_level = serializers.IntegerField(required=False, min_value=0, max_value=100000)

    class Meta:
        model = StoreVariant
        fields = [
            "id",
            "product",
            "sku",
            "colour_name",
            "colour_hex",
            "size_label",
            "price_syp",
            "price_usd",
            "max_per_order",
            "is_active",
            "sort_order",
            "inventory_item",
            "stock",
            "reorder_level",
            "initial_stock",
        ]
        read_only_fields = ["inventory_item"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["reorder_level"] = int(instance.inventory_item.reorder_level) if instance.inventory_item else 0
        return data

    def _apply_reorder(self, variant, level):
        if level is not None and variant.inventory_item is not None:
            variant.inventory_item.reorder_level = level
            variant.inventory_item.save(update_fields=["reorder_level", "updated_at"])

    def get_stock(self, obj) -> int:
        return obj.available

    def validate_colour_hex(self, value):
        return value.upper()

    def validate(self, attrs):
        product = attrs.get("product") or (self.instance.product if self.instance else None)
        if product is not None and product.is_deleted:
            raise serializers.ValidationError({"product": "This product was deleted."})
        if self.instance and "product" in attrs and attrs["product"].pk != self.instance.product_id:
            raise serializers.ValidationError({"product": "A variant cannot move to another product."})
        colour = attrs.get("colour_name", self.instance.colour_name if self.instance else "")
        size = attrs.get("size_label", self.instance.size_label if self.instance else "")
        clash = StoreVariant.objects.filter(product=product, colour_name=colour, size_label=size, is_deleted=False)
        sku = attrs.get("sku", self.instance.sku if self.instance else "")
        sku_clash = StoreVariant.objects.filter(sku=sku, is_deleted=False) if sku else StoreVariant.objects.none()
        if self.instance:
            clash, sku_clash = clash.exclude(pk=self.instance.pk), sku_clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("This colour and size already exist for the product.")
        if sku_clash.exists():
            raise serializers.ValidationError({"sku": "This SKU is already used."})
        return attrs

    def create(self, validated_data):
        initial = validated_data.pop("initial_stock", 0)
        level = validated_data.pop("reorder_level", None)
        variant = super().create(validated_data)
        ensure_inventory_item(variant, initial_stock=initial, actor=self.context["request"].user)
        self._apply_reorder(variant, level)
        return variant

    def update(self, instance, validated_data):
        validated_data.pop("initial_stock", None)
        level = validated_data.pop("reorder_level", None)
        variant = super().update(instance, validated_data)
        sync_inventory_item(variant)
        self._apply_reorder(variant, level)
        return variant


class StockAdjustSerializer(serializers.Serializer):
    movement_type = serializers.ChoiceField(choices=["purchase", "adjustment", "waste"])
    quantity = serializers.IntegerField(help_text="Positive; SIGNED for an adjustment.")
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")

    def validate(self, attrs):
        if attrs["quantity"] == 0:
            raise serializers.ValidationError({"quantity": "The quantity cannot be zero."})
        if attrs["movement_type"] != "adjustment" and attrs["quantity"] < 0:
            raise serializers.ValidationError({"quantity": "Use a positive quantity."})
        return attrs


def _checked_image(value, field: str):
    if not value:
        return value
    from PIL import Image

    value = validate_image_upload(value, field)
    try:
        value.seek(0)
        with Image.open(value) as picture:
            width, height = picture.size
        value.seek(0)
    except Exception:  # noqa: BLE001 - any decoder failure means "not a usable image"
        raise serializers.ValidationError("This file is not a readable image.") from None
    if width <= 0 or height <= 0 or width * height > int(settings.CHAT_IMAGE_MAX_PIXELS):
        raise serializers.ValidationError("The image dimensions are not supported.")
    return value


class DashboardImageSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()
    thumb_url = serializers.SerializerMethodField()

    class Meta:
        model = StoreProductImage
        fields = ["id", "product", "variant", "image", "url", "thumb_url", "alt_text", "sort_order", "is_primary"]
        extra_kwargs = {"image": {"write_only": True}}

    def get_url(self, obj) -> str:
        return image_url(obj, self.context.get("request"))

    def get_thumb_url(self, obj) -> str:
        return image_url(obj, self.context.get("request"), thumb=True)

    def validate_image(self, value):
        return _checked_image(value, "image")

    @staticmethod
    def _demote_other_primaries(product_id, keep_pk, wants_primary) -> None:
        if wants_primary:  # the unique "one primary per product" rule must hold at every step
            StoreProductImage.objects.filter(product_id=product_id, is_primary=True, is_deleted=False).exclude(
                pk=keep_pk
            ).update(is_primary=False)

    def create(self, validated_data):
        self._demote_other_primaries(validated_data["product"].pk, None, validated_data.get("is_primary"))
        return super().create(validated_data)

    def update(self, instance, validated_data):
        self._demote_other_primaries(instance.product_id, instance.pk, validated_data.get("is_primary"))
        return super().update(instance, validated_data)

    def validate(self, attrs):
        product = attrs.get("product") or (self.instance.product if self.instance else None)
        variant = attrs.get("variant")
        if variant is not None and product is not None and variant.product_id != product.pk:
            raise serializers.ValidationError({"variant": "The variant belongs to another product."})
        if self.instance and "product" in attrs and attrs["product"].pk != self.instance.product_id:
            raise serializers.ValidationError({"product": "An image cannot move to another product."})
        return attrs


class DashboardOrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = StoreOrderItem
        fields = [
            "id",
            "product",
            "variant",
            "name",
            "brand_name",
            "variant_label",
            "sku",
            "unit_price",
            "quantity",
            "line_total",
        ]


class DashboardOrderSerializer(serializers.ModelSerializer):
    code = serializers.CharField(read_only=True)
    customer = serializers.SerializerMethodField()
    items = DashboardOrderItemSerializer(many=True, read_only=True)
    history = serializers.SerializerMethodField()

    class Meta:
        model = StoreOrder
        fields = [
            "id",
            "code",
            "status",
            "customer",
            "currency",
            "subtotal",
            "rounding_adjustment",
            "total",
            "notes",
            "internal_notes",
            "cancel_reason",
            "cancelled_by_customer",
            "created_at",
            "confirmed_at",
            "ready_at",
            "picked_up_at",
            "cancelled_at",
            "items",
            "history",
        ]

    def get_customer(self, obj) -> dict:
        return {"id": obj.user_id, "full_name": obj.user.full_name, "phone_number": obj.user.phone_number}

    @extend_schema_field(HistorySerializer(many=True))
    def get_history(self, obj):
        return HistorySerializer(obj.history.all(), many=True).data


class DashboardOrderListSerializer(DashboardOrderSerializer):
    items_count = serializers.SerializerMethodField()

    class Meta(DashboardOrderSerializer.Meta):
        fields = [f for f in DashboardOrderSerializer.Meta.fields if f not in {"items", "history"}] + ["items_count"]

    def get_items_count(self, obj) -> int:
        return sum(item.quantity for item in obj.items.all())


class OrderStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["confirmed", "ready_for_pickup", "picked_up", "cancelled"])
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    note = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")


class DashboardReviewSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    author = serializers.SerializerMethodField()

    class Meta:
        model = StoreReview
        fields = [
            "id",
            "product",
            "product_name",
            "author",
            "rating",
            "text",
            "edit_count",
            "is_hidden",
            "hidden_reason",
            "moderated_at",
            "created_at",
        ]

    def get_author(self, obj) -> dict:
        return {"id": obj.user_id, "full_name": obj.user.full_name}


class HideReviewSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
