from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from decimal import Decimal
from typing import Any, cast

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Sum
from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.common.file_validation import validate_document_upload
from apps.files.models import FileResource
from apps.files.services import accessible_files_for_user
from apps.finance.models import FinanceCurrency, PaymentMethod

from .models import (
    PrintBinding,
    PrintBindingPrice,
    PrintColorMode,
    PrintConversion,
    PrintConversionStatus,
    PrintCoverOption,
    PrintJob,
    PrintOrder,
    PrintOrderItem,
    PrintOrderStatus,
    PrintOrderStatusHistory,
    PrintPaperSize,
    PrintPickupLocation,
    PrintPricingRule,
    PrintSides,
    ReadyProduct,
    SpiralWireSpec,
)
from .options import (
    MAX_PAGE_RANGE_LENGTH,
    PAGES_PER_SHEET_CHOICES,
    DuplexFlip,
    NUpDirection,
    PrintOrientation,
)
from .services import (
    COUNTER_STATUSES,
    CounterOrderService,
    PrintOrderService,
    PrintPricingService,
    PrintStatusService,
)


class PrintConvertUploadSerializer(serializers.Serializer):
    file = serializers.FileField(help_text="A Word (doc, docx) or PowerPoint (ppt, pptx) file to convert to PDF.")


def available_binding_codes() -> set[str]:
    """Binding codes a customer may choose: the shop's configured types plus the built-in ones (an unpriced one is reported
    as "unavailable" when the order is priced, as before)."""

    configured = set(
        PrintBindingPrice.objects.filter(is_active=True, is_deleted=False).values_list("binding", flat=True)
    )
    return configured | set(PrintBinding.values)


class PrintOrderItemInputSerializer(serializers.Serializer):
    source_file = serializers.PrimaryKeyRelatedField(
        queryset=FileResource.objects.none(), required=False, allow_null=True
    )
    uploaded_file = serializers.FileField(required=False, allow_null=True, write_only=True)
    converted_upload = serializers.UUIDField(required=False, allow_null=True, write_only=True)
    product = serializers.PrimaryKeyRelatedField(
        queryset=ReadyProduct.objects.filter(is_active=True, is_deleted=False),
        required=False,
        allow_null=True,
        help_text="A ready-made product: its specification and price are fixed.",
    )
    copies = serializers.IntegerField(min_value=1, max_value=99, default=1)
    color_mode = serializers.ChoiceField(
        choices=cast(Sequence[Any], PrintOrderItem._meta.get_field("color_mode").choices), required=False
    )
    paper_size = serializers.ChoiceField(
        choices=cast(Sequence[Any], PrintOrderItem._meta.get_field("paper_size").choices), required=False
    )
    sides = serializers.ChoiceField(
        choices=cast(Sequence[Any], PrintOrderItem._meta.get_field("sides").choices), required=False
    )
    binding = serializers.CharField(max_length=32, required=False)
    cover = serializers.CharField(max_length=32, required=False, allow_blank=True)
    pages_per_sheet = serializers.ChoiceField(choices=PAGES_PER_SHEET_CHOICES, default=1)
    n_up_direction = serializers.ChoiceField(choices=NUpDirection.choices, default=NUpDirection.LTR)
    duplex_flip = serializers.ChoiceField(choices=DuplexFlip.choices, default=DuplexFlip.LONG_EDGE)
    orientation = serializers.ChoiceField(choices=PrintOrientation.choices, default=PrintOrientation.AUTO)
    page_range = serializers.CharField(max_length=MAX_PAGE_RANGE_LENGTH, required=False, allow_blank=True)
    collate = serializers.BooleanField(default=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            source_file = cast(serializers.PrimaryKeyRelatedField, self.fields["source_file"])
            source_file.queryset = accessible_files_for_user(request.user).filter(is_printable=True)

    def validate_binding(self, value):
        if value not in available_binding_codes():
            raise serializers.ValidationError("This binding type is not offered.")
        return value

    def validate_uploaded_file(self, value):
        return validate_document_upload(value, "uploaded_file") if value else value

    def _resolve_conversion(self, token):
        request = self.context.get("request")
        conversion = PrintConversion.objects.filter(
            token=token,
            user=getattr(request, "user", None),
            status=PrintConversionStatus.READY,
            is_deleted=False,
            expires_at__gt=timezone.now(),
        ).first()
        if not conversion or not conversion.pdf_file:
            raise serializers.ValidationError({"converted_upload": "The converted document is not available."})
        with conversion.pdf_file.open("rb") as handle:
            content = handle.read()
        return SimpleUploadedFile(conversion.pdf_name, content, content_type="application/pdf")

    def validate(self, attrs):
        token = attrs.pop("converted_upload", None)
        if token:
            if attrs.get("uploaded_file"):
                raise serializers.ValidationError("Send either uploaded_file or converted_upload, not both.")
            attrs["uploaded_file"] = self._resolve_conversion(token)
        if attrs.get("product"):
            if attrs.get("source_file") or attrs.get("uploaded_file"):
                raise serializers.ValidationError("A ready-made product does not take a file.")
            return attrs
        if bool(attrs.get("source_file")) == bool(attrs.get("uploaded_file")):
            raise serializers.ValidationError("Exactly one source_file, uploaded_file or converted_upload is required.")
        missing = {
            name: "This field is required."
            for name in ("color_mode", "paper_size", "sides", "binding")
            if name not in attrs
        }
        if missing:
            raise serializers.ValidationError(missing)
        return attrs


class PrintOrderItemSerializer(serializers.ModelSerializer):
    source_file_title = serializers.CharField(source="source_file.title", read_only=True)
    has_uploaded_file = serializers.SerializerMethodField()
    preview_ticket_endpoint = serializers.SerializerMethodField()

    class Meta:
        model = PrintOrderItem
        fields = [
            "id",
            "source_file",
            "source_file_title",
            "has_uploaded_file",
            "preview_ticket_endpoint",
            "original_file_name",
            "file_type",
            "file_size",
            "pages_count",
            "copies",
            "color_mode",
            "paper_size",
            "sides",
            "binding",
            "binding_note",
            "pages_per_sheet",
            "n_up_direction",
            "duplex_flip",
            "orientation",
            "page_range",
            "selected_pages",
            "collate",
            "cover",
            "cover_price",
            "product",
            "sheets_count",
            "unit_price",
            "binding_price",
            "price",
            "pricing_snapshot",
            "created_at",
        ]
        read_only_fields = fields

    def get_has_uploaded_file(self, obj) -> bool:
        return bool(obj.uploaded_file)

    def get_preview_ticket_endpoint(self, obj) -> str:
        return f"/api/v1/printing/items/{obj.pk}/access-ticket/"


class MobilePrintOrderStatusHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = PrintOrderStatusHistory
        fields = ["id", "old_status", "new_status", "public_note", "created_at"]
        read_only_fields = fields


class DashboardPrintOrderStatusHistorySerializer(serializers.ModelSerializer):
    changed_by_name = serializers.CharField(source="changed_by.full_name", read_only=True)

    class Meta:
        model = PrintOrderStatusHistory
        fields = [
            "id",
            "old_status",
            "new_status",
            "changed_by",
            "changed_by_name",
            "public_note",
            "internal_note",
            "created_at",
        ]
        read_only_fields = fields


class PrintPickupLocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = PrintPickupLocation
        fields = ["id", "name", "address", "instructions", "is_active", "sort_order"]
        read_only_fields = ["id"]


class PrintPricingRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = PrintPricingRule
        fields = "__all__"
        read_only_fields = ["id", "created_at", "updated_at", "is_deleted", "deleted_at"]

    def validate(self, attrs):
        instance = self.instance or PrintPricingRule()
        for key, value in attrs.items():
            setattr(instance, key, value)
        instance.clean()
        return attrs


class PrintBindingPriceSerializer(serializers.ModelSerializer):
    class Meta:
        model = PrintBindingPrice
        fields = "__all__"
        read_only_fields = ["id", "created_at", "updated_at", "is_deleted", "deleted_at"]

    def validate(self, attrs):
        instance = self.instance or PrintBindingPrice()
        for key, value in attrs.items():
            setattr(instance, key, value)
        instance.clean()
        return attrs


def _rounding_adjustment(order: PrintOrder) -> str:
    """How much the order total was rounded (signed, in the order currency), as an exact string."""

    return str((order.pricing_snapshot or {}).get("rounding_adjustment") or "0.00")


class MobilePrintOrderSerializer(serializers.ModelSerializer):
    items = PrintOrderItemSerializer(many=True, read_only=True)
    status_history = MobilePrintOrderStatusHistorySerializer(many=True, read_only=True)
    pickup_location_detail = PrintPickupLocationSerializer(source="pickup_location", read_only=True)
    rounding_adjustment = serializers.SerializerMethodField()

    class Meta:
        model = PrintOrder
        fields = [
            "id",
            "status",
            "priority",
            "total_price",
            "rounding_adjustment",
            "currency",
            "pricing_revision",
            "price_calculated_at",
            "pickup_location",
            "pickup_location_detail",
            "user_notes",
            "completed_at",
            "cancelled_at",
            "rejected_reason",
            "items",
            "status_history",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField())
    def get_rounding_adjustment(self, obj: PrintOrder) -> str:
        return _rounding_adjustment(obj)


class DashboardPrintOrderSerializer(serializers.ModelSerializer):
    items = PrintOrderItemSerializer(many=True, read_only=True)
    status_history = DashboardPrintOrderStatusHistorySerializer(many=True, read_only=True)
    user_name = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    assigned_to_name = serializers.CharField(source="assigned_to.full_name", read_only=True)
    pickup_location_detail = PrintPickupLocationSerializer(source="pickup_location", read_only=True)
    paid_amount = serializers.SerializerMethodField()
    due_amount = serializers.SerializerMethodField()
    rounding_adjustment = serializers.SerializerMethodField()
    stock_usage = serializers.SerializerMethodField()

    class Meta:
        model = PrintOrder
        fields = [
            "id",
            "user",
            "user_name",
            "source",
            "customer_name",
            "customer_phone",
            "created_by_name",
            "status",
            "priority",
            "total_price",
            "rounding_adjustment",
            "paid_amount",
            "due_amount",
            "currency",
            "pricing_snapshot",
            "pricing_revision",
            "price_calculated_at",
            "pickup_location",
            "pickup_location_detail",
            "stock_deducted_at",
            "stock_usage",
            "user_notes",
            "internal_notes",
            "assigned_to",
            "assigned_to_name",
            "completed_at",
            "cancelled_at",
            "rejected_reason",
            "items",
            "status_history",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField())
    def get_user_name(self, obj: PrintOrder) -> str:
        return obj.customer_display

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_created_by_name(self, obj: PrintOrder) -> str | None:
        return obj.created_by.full_name if obj.created_by else None

    def _paid(self, obj: PrintOrder) -> Decimal:
        annotated = getattr(obj, "paid_total", None)  # the list view annotates it: no query per row
        if annotated is None:
            annotated = obj.payments.filter(is_deleted=False).aggregate(total=Sum("amount"))["total"]
        return Decimal(annotated or 0).quantize(Decimal("0.01"))

    @extend_schema_field(serializers.CharField())
    def get_paid_amount(self, obj: PrintOrder) -> str:
        return str(self._paid(obj))

    @extend_schema_field(serializers.CharField())
    def get_due_amount(self, obj: PrintOrder) -> str:
        return str(max(Decimal(obj.total_price) - self._paid(obj), Decimal("0")).quantize(Decimal("0.01")))

    @extend_schema_field(serializers.CharField())
    def get_rounding_adjustment(self, obj: PrintOrder) -> str:
        return _rounding_adjustment(obj)

    @extend_schema_field(serializers.ListField(child=serializers.DictField(child=serializers.CharField())))
    def get_stock_usage(self, obj: PrintOrder) -> list[dict[str, str]]:
        """What this order took from the shelf when it was delivered."""

        return [
            {"item": move.item.name, "unit": move.item.unit, "quantity": str(abs(move.quantity))}
            for move in obj.stock_movements.all()
            if not move.is_deleted
        ]


class BasePrintRequestSerializer(serializers.Serializer):
    items = PrintOrderItemInputSerializer(many=True)
    currency = serializers.ChoiceField(
        choices=FinanceCurrency.choices,
        required=False,
        help_text="Price the order in this currency (default: the shop default, new Syrian pound).",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request and "items" in self.fields:
            items = cast(serializers.ListSerializer, self.fields["items"])
            item_serializer = cast(PrintOrderItemInputSerializer, items.child)
            item_serializer.context.update({"request": request})
            source_file = cast(serializers.PrimaryKeyRelatedField, item_serializer.fields["source_file"])
            source_file.queryset = accessible_files_for_user(request.user).filter(is_printable=True)

    def to_internal_value(self, data):
        # QueryDict.copy() deep-copies, which fails for large uploads (disk-backed temporary files cannot be pickled).
        mutable = copy.copy(data)
        if isinstance(mutable.get("items"), str):
            try:
                mutable["items"] = json.loads(mutable["items"])
            except json.JSONDecodeError as exc:
                raise serializers.ValidationError({"items": "Items must be valid JSON."}) from exc
        return super().to_internal_value(mutable)

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError("At least one print item is required.")
        if len(value) > 10:
            raise serializers.ValidationError("A single order cannot contain more than 10 items.")
        return value


class PrintQuoteSerializer(BasePrintRequestSerializer):
    def create(self, validated_data):
        return PrintPricingService.quote(
            self.context["request"].user, validated_data["items"], validated_data.get("currency")
        )


class PrintOrderCreateSerializer(BasePrintRequestSerializer):
    user_notes = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    pickup_location = serializers.PrimaryKeyRelatedField(
        queryset=PrintPickupLocation.objects.filter(is_active=True, is_deleted=False),
        required=False,
        allow_null=True,
    )

    def create(self, validated_data):
        return PrintOrderService.create_order(
            self.context["request"].user,
            validated_data["items"],
            validated_data.get("user_notes", ""),
            pickup_location=validated_data.get("pickup_location"),
            request=self.context["request"],
            idempotency_key=self._idempotency_key(),
            currency=validated_data.get("currency"),
        )

    def _idempotency_key(self) -> str:
        key = self.context["request"].headers.get("Idempotency-Key", "").strip()
        if len(key) > 255:
            raise serializers.ValidationError({"Idempotency-Key": "Header must not exceed 255 characters."})
        return key


class PrintStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=PrintOrderStatus.choices)
    public_note = serializers.CharField(required=False, allow_blank=True, max_length=1000)
    internal_note = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    rejected_reason = serializers.CharField(required=False, allow_blank=True, max_length=2000)

    def save(self, **kwargs):
        return PrintStatusService.change_status(
            self.context["order"],
            self.validated_data["status"],
            self.context["request"].user,
            public_note=self.validated_data.get("public_note", ""),
            internal_note=self.validated_data.get("internal_note", ""),
            rejected_reason=self.validated_data.get("rejected_reason", ""),
            request=self.context["request"],
        )


class PrintOrderAssignSerializer(serializers.Serializer):
    assigned_to = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(
            role__in=[UserRole.PRINT_STAFF, UserRole.ADMIN, UserRole.IT_SUPPORT],
            is_active=True,
            is_deleted=False,
        )
    )

    def save(self, **kwargs):
        order = self.context["order"]
        order.assigned_to = self.validated_data["assigned_to"]
        order.save(update_fields=["assigned_to", "updated_at"])
        return order


class PrintOrderNoteSerializer(serializers.Serializer):
    internal_notes = serializers.CharField(max_length=4000)

    def save(self, **kwargs):
        order = self.context["order"]
        order.internal_notes = self.validated_data["internal_notes"]
        order.save(update_fields=["internal_notes", "updated_at"])
        return order


class CounterItemSerializer(serializers.Serializer):
    """One line typed in at the counter: there is no file, only what is being printed."""

    title = serializers.CharField(
        max_length=255, required=False, allow_blank=True, help_text="What it is, for the slip."
    )
    product = serializers.PrimaryKeyRelatedField(
        queryset=ReadyProduct.objects.filter(is_active=True, is_deleted=False),
        required=False,
        allow_null=True,
        help_text="A ready-made product: its specification and fixed price are used.",
    )
    pages_count = serializers.IntegerField(
        min_value=1, max_value=100000, required=False, help_text="Pages of ONE copy."
    )
    copies = serializers.IntegerField(min_value=1, max_value=99)
    color_mode = serializers.ChoiceField(choices=PrintColorMode.choices, required=False)
    paper_size = serializers.ChoiceField(choices=PrintPaperSize.choices, required=False)
    sides = serializers.ChoiceField(choices=PrintSides.choices, required=False)
    binding = serializers.CharField(max_length=32, default=PrintBinding.NONE)
    cover = serializers.CharField(max_length=32, required=False, allow_blank=True)
    pages_per_sheet = serializers.ChoiceField(choices=PAGES_PER_SHEET_CHOICES, default=1)
    binding_note = serializers.CharField(max_length=120, required=False, allow_blank=True)

    def validate_binding(self, value):
        if value not in available_binding_codes():
            raise serializers.ValidationError("This binding type is not offered.")
        return value

    def validate(self, attrs):
        if attrs.get("product"):
            return attrs
        missing = {
            name: "This field is required."
            for name in ("pages_count", "color_mode", "paper_size", "sides")
            if name not in attrs
        }
        if missing:
            raise serializers.ValidationError(missing)
        return attrs


class CounterQuoteSerializer(serializers.Serializer):
    items = CounterItemSerializer(many=True)
    currency = serializers.ChoiceField(choices=FinanceCurrency.choices, required=False)

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError("At least one line is required.")
        if len(value) > 20:
            raise serializers.ValidationError("A counter order cannot contain more than 20 lines.")
        return value


class CounterOrderCreateSerializer(CounterQuoteSerializer):
    customer_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    customer_phone = serializers.CharField(max_length=32, required=False, allow_blank=True)
    total_price = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0"),
        required=False,
        help_text="The price taken from the customer. Defaults to the rounded list price.",
    )
    status = serializers.ChoiceField(choices=[(value, value) for value in COUNTER_STATUSES], default="delivered")
    paid_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0"),
        required=False,
        help_text="What the customer has paid now. Defaults to the full price when the order is delivered.",
    )
    payment_method = serializers.ChoiceField(choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    notes = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    internal_notes = serializers.CharField(max_length=4000, required=False, allow_blank=True)

    def save(self, **kwargs):
        data = self.validated_data
        return CounterOrderService.create(
            self.context["request"].user,
            [dict(item) for item in data["items"]],
            customer_name=data.get("customer_name", ""),
            customer_phone=data.get("customer_phone", ""),
            currency=data.get("currency"),
            total_price=data.get("total_price"),
            status_value=data["status"],
            paid_amount=data.get("paid_amount"),
            payment_method=data["payment_method"],
            notes=data.get("notes", ""),
            internal_notes=data.get("internal_notes", ""),
            request=self.context["request"],
        )


class SpiralWireSpecSerializer(serializers.ModelSerializer):
    class Meta:
        model = SpiralWireSpec
        fields = "__all__"
        read_only_fields = ["id", "created_at", "updated_at", "is_deleted", "deleted_at"]

    def validate(self, attrs):
        instance = self.instance or SpiralWireSpec()
        for key, value in attrs.items():
            setattr(instance, key, value)
        instance.clean()
        return attrs


class PrintCoverOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PrintCoverOption
        fields = "__all__"
        read_only_fields = ["id", "created_at", "updated_at", "is_deleted", "deleted_at"]

    def validate(self, attrs):
        instance = self.instance or PrintCoverOption()
        for key, value in attrs.items():
            setattr(instance, key, value)
        instance.clean()
        return attrs


class ReadyProductSerializer(serializers.ModelSerializer):
    """Staff view of a product (all fields) used by the dashboard catalog."""

    image = serializers.ImageField(required=False, allow_null=True)
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = ReadyProduct
        fields = "__all__"
        read_only_fields = ["id", "created_at", "updated_at", "is_deleted", "deleted_at"]

    def get_image_url(self, obj) -> str | None:
        request = self.context.get("request")
        if not obj.image:
            return None
        return request.build_absolute_uri(obj.image.url) if request else obj.image.url

    def validate_image(self, value):
        from apps.common.file_validation import validate_image_upload

        return validate_image_upload(value, "image") if value else value

    def validate(self, attrs):
        instance = self.instance or ReadyProduct()
        for key, value in attrs.items():
            setattr(instance, key, value)
        instance.clean()
        return attrs


class PublicReadyProductSerializer(serializers.ModelSerializer):
    """What a customer sees of a product (no internal fields)."""

    image_url = serializers.SerializerMethodField()

    class Meta:
        model = ReadyProduct
        fields = [
            "id",
            "name",
            "description",
            "image_url",
            "pages_count",
            "paper_size",
            "color_mode",
            "sides",
            "binding",
            "cover",
            "price",
            "currency",
        ]
        read_only_fields = fields

    def get_image_url(self, obj) -> str | None:
        request = self.context.get("request")
        if not obj.image:
            return None
        return request.build_absolute_uri(obj.image.url) if request else obj.image.url


class PrintQueueJobSerializer(serializers.ModelSerializer):
    order_id = serializers.IntegerField(read_only=True)
    item_name = serializers.CharField(source="item.original_file_name", read_only=True)
    created_by_name = serializers.CharField(source="created_by.full_name", read_only=True, default=None)

    class Meta:
        model = PrintJob
        fields = [
            "id",
            "order_id",
            "item",
            "item_name",
            "status",
            "printer_name",
            "sides_printed",
            "sheets",
            "options",
            "error",
            "created_by_name",
            "started_at",
            "finished_at",
            "created_at",
        ]
        read_only_fields = fields


class PrintJobStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["printing", "done", "failed"])
    printer_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    error = serializers.CharField(max_length=255, required=False, allow_blank=True)
