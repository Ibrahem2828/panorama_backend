from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers

from apps.finance.models import InventoryItem, PaymentMethod

from .models import StationerySale, StationerySaleLine
from .services import StationerySaleService


class SellableItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = InventoryItem
        fields = ["id", "name", "sku", "unit", "quantity", "sale_price", "currency"]
        read_only_fields = fields


class SaleLineInputSerializer(serializers.Serializer):
    item = serializers.IntegerField(min_value=1)
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))


class SaleCreateSerializer(serializers.Serializer):
    lines = SaleLineInputSerializer(many=True)
    payment_method = serializers.ChoiceField(choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    customer_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    note = serializers.CharField(max_length=255, required=False, allow_blank=True)

    def save(self, **kwargs):
        data = self.validated_data
        return StationerySaleService.record(
            self.context["request"].user,
            [dict(line) for line in data["lines"]],
            payment_method=data["payment_method"],
            customer_name=data.get("customer_name", ""),
            note=data.get("note", ""),
            request=self.context["request"],
        )


class SaleLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = StationerySaleLine
        fields = ["id", "item", "name", "quantity", "unit_price", "line_total"]
        read_only_fields = fields


class SaleSerializer(serializers.ModelSerializer):
    lines = SaleLineSerializer(many=True, read_only=True)
    sold_by_name = serializers.CharField(source="sold_by.full_name", read_only=True, default=None)

    class Meta:
        model = StationerySale
        fields = [
            "id",
            "sold_at",
            "currency",
            "total",
            "payment_method",
            "customer_name",
            "note",
            "sold_by_name",
            "lines",
        ]
        read_only_fields = fields
