from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers

from apps.common.file_validation import validate_document_upload
from apps.printing.models import PrintOrder

from .models import (
    ExchangeRate,
    Expense,
    FinanceCurrency,
    InventoryItem,
    OrderPayment,
    PurchaseReminder,
    StockMovement,
    StockMovementType,
    WasteReason,
)
from .services import PaymentService, PurchaseReminderService, money


class ExchangeRateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExchangeRate
        fields = ["id", "effective_from", "syp_per_usd", "note", "created_at"]
        read_only_fields = ["id", "created_at"]


class InventoryItemSerializer(serializers.ModelSerializer):
    opening_quantity = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal("0"), write_only=True, required=False
    )
    is_low = serializers.BooleanField(read_only=True)
    stock_value = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)

    class Meta:
        model = InventoryItem
        fields = [
            "id",
            "name",
            "category",
            "unit",
            "sku",
            "quantity",
            "opening_quantity",
            "consumption_key",
            "consumption_per_use",
            "pending_consumption",
            "reorder_level",
            "unit_cost",
            "currency",
            "stock_value",
            "is_low",
            "location",
            "notes",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "quantity",
            "pending_consumption",
            "stock_value",
            "is_low",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        name = attrs.get("name", getattr(self.instance, "name", None))
        category = attrs.get("category", getattr(self.instance, "category", None))
        clash = InventoryItem.objects.filter(is_deleted=False, name=name, category=category)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError({"name": "An item with this name already exists in the category."})
        # The unique constraint is conditional (live rows, non-empty key); check it here so a duplicate answers 400, not a DB error.
        key = attrs.get("consumption_key", getattr(self.instance, "consumption_key", ""))
        if key.startswith("ink_") and key != getattr(self.instance, "consumption_key", ""):
            raise serializers.ValidationError(
                {"consumption_key": "Ink is tracked as purchases, not as per-page consumption."}
            )
        if key:
            taken = InventoryItem.objects.filter(is_deleted=False, consumption_key=key)
            if self.instance:
                taken = taken.exclude(pk=self.instance.pk)
            if taken.exists():
                raise serializers.ValidationError(
                    {"consumption_key": "Another item already uses this consumption key."}
                )
        return attrs

    def create(self, validated_data):
        opening = validated_data.pop("opening_quantity", Decimal("0"))
        actor = self.context["request"].user
        item = super().create(validated_data)
        if opening:
            from .services import InventoryService

            InventoryService.record_movement(
                item, StockMovementType.ADJUSTMENT, opening, actor=actor, note="Opening balance"
            )
            item.refresh_from_db()
        return item


class StockMovementSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source="item.name", read_only=True)
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = StockMovement
        fields = [
            "id",
            "item",
            "item_name",
            "movement_type",
            "quantity",
            "quantity_after",
            "unit_cost",
            "currency",
            "note",
            "waste_reason",
            "order",
            "created_by_name",
            "created_at",
        ]
        read_only_fields = fields

    def get_created_by_name(self, obj: StockMovement) -> str | None:
        return obj.created_by.full_name if obj.created_by else None


class StockMovementCreateSerializer(serializers.Serializer):
    movement_type = serializers.ChoiceField(choices=StockMovementType.choices)
    quantity = serializers.DecimalField(max_digits=14, decimal_places=2)
    unit_cost = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0"), required=False)
    currency = serializers.ChoiceField(choices=FinanceCurrency.choices, required=False)
    note = serializers.CharField(max_length=255, required=False, allow_blank=True)
    waste_reason = serializers.ChoiceField(choices=WasteReason.choices, required=False)
    record_expense = serializers.BooleanField(
        required=False, default=False, help_text="For a purchase: also book quantity x unit cost as a supplies expense."
    )

    def validate(self, attrs):
        if attrs["movement_type"] == StockMovementType.PURCHASE and attrs.get("record_expense"):
            if attrs.get("unit_cost") is None:
                raise serializers.ValidationError({"unit_cost": "A unit cost is needed to record the expense."})
        return attrs


class PurchaseReminderSerializer(serializers.ModelSerializer):
    last_purchase = serializers.SerializerMethodField()
    weeks_since = serializers.SerializerMethodField()
    due_on = serializers.SerializerMethodField()
    is_due = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseReminder
        fields = [
            "id",
            "name",
            "interval_weeks",
            "is_active",
            "last_purchase",
            "weeks_since",
            "due_on",
            "is_due",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def _info(self, obj: PurchaseReminder) -> dict:
        cache = self.__dict__.setdefault("_status_cache", {})
        if obj.pk not in cache:
            cache[obj.pk] = PurchaseReminderService.status(obj)
        return cache[obj.pk]

    def get_last_purchase(self, obj) -> str | None:
        value = self._info(obj)["last_purchase"]
        return value.isoformat() if value else None

    def get_weeks_since(self, obj) -> int | None:
        return self._info(obj)["weeks_since"]

    def get_due_on(self, obj) -> str | None:
        value = self._info(obj)["due_on"]
        return value.isoformat() if value else None

    def get_is_due(self, obj) -> bool:
        return self._info(obj)["is_due"]


class ExpenseSerializer(serializers.ModelSerializer):
    reminder_name = serializers.CharField(source="reminder.name", read_only=True, default=None)
    has_invoice = serializers.SerializerMethodField()
    invoice = serializers.FileField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = Expense
        fields = [
            "id",
            "spent_on",
            "category",
            "kind",
            "description",
            "amount",
            "currency",
            "supplier",
            "notes",
            "invoice",
            "has_invoice",
            "reminder",
            "reminder_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_has_invoice(self, obj: Expense) -> bool:
        return bool(obj.invoice)

    def validate_invoice(self, value):
        return validate_document_upload(value, "invoice") if value else value


class OrderPaymentSerializer(serializers.ModelSerializer):
    received_by_name = serializers.SerializerMethodField()

    class Meta:
        model = OrderPayment
        fields = [
            "id",
            "order",
            "amount",
            "currency",
            "method",
            "received_at",
            "note",
            "received_by_name",
            "created_at",
        ]
        read_only_fields = ["id", "currency", "received_at", "received_by_name", "created_at"]

    def get_received_by_name(self, obj: OrderPayment) -> str | None:
        return obj.received_by.full_name if obj.received_by else None

    def validate_order(self, order: PrintOrder) -> PrintOrder:
        if order.is_deleted:
            raise serializers.ValidationError("Unknown order.")
        return order

    def create(self, validated_data):
        return PaymentService.record(
            validated_data["order"],
            amount=validated_data["amount"],
            method=validated_data.get("method", "cash"),
            actor=self.context["request"].user,
            note=validated_data.get("note", ""),
        )


class PaymentVoidSerializer(serializers.Serializer):
    reason = serializers.CharField(min_length=5, max_length=255)


class PrintJobSerializer(serializers.Serializer):
    """One finished print order as the cashier sees it."""

    id = serializers.IntegerField()
    customer = serializers.CharField()
    completed_at = serializers.DateTimeField()
    files_count = serializers.IntegerField()
    pages_count = serializers.IntegerField()
    sheets_count = serializers.IntegerField()
    total_price = serializers.DecimalField(max_digits=14, decimal_places=2)
    currency = serializers.CharField()
    paid = serializers.DecimalField(max_digits=14, decimal_places=2)
    due = serializers.DecimalField(max_digits=14, decimal_places=2)
    is_paid = serializers.BooleanField()


def print_job_row(order: PrintOrder, *, files: int, pages: int, sheets: int, paid) -> dict:
    total = money(order.total_price)
    paid = money(paid)
    return {
        "id": order.id,
        "customer": order.customer_display,
        "completed_at": order.completed_at,
        "files_count": files,
        "pages_count": pages,
        "sheets_count": sheets,
        "total_price": total,
        "currency": order.currency,
        "paid": paid,
        "due": max(total - paid, Decimal("0")),
        "is_paid": paid >= total,
    }
