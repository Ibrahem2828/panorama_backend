from __future__ import annotations

import json

from rest_framework import serializers

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.common.file_validation import validate_document_upload
from apps.files.models import FileResource
from apps.files.services import accessible_files_for_user

from .models import (
    PrintBindingPrice,
    PrintOrder,
    PrintOrderItem,
    PrintOrderStatus,
    PrintOrderStatusHistory,
    PrintPickupLocation,
    PrintPricingRule,
)
from .services import PrintOrderService, PrintPricingService, PrintStatusService


class PrintOrderItemInputSerializer(serializers.Serializer):
    source_file = serializers.PrimaryKeyRelatedField(
        queryset=FileResource.objects.none(), required=False, allow_null=True
    )
    uploaded_file = serializers.FileField(required=False, allow_null=True, write_only=True)
    copies = serializers.IntegerField(min_value=1, max_value=99, default=1)
    color_mode = serializers.ChoiceField(choices=PrintOrderItem._meta.get_field("color_mode").choices)
    paper_size = serializers.ChoiceField(choices=PrintOrderItem._meta.get_field("paper_size").choices)
    sides = serializers.ChoiceField(choices=PrintOrderItem._meta.get_field("sides").choices)
    binding = serializers.ChoiceField(choices=PrintOrderItem._meta.get_field("binding").choices)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            self.fields["source_file"].queryset = accessible_files_for_user(request.user).filter(is_printable=True)

    def validate_uploaded_file(self, value):
        return validate_document_upload(value, "uploaded_file") if value else value

    def validate(self, attrs):
        if bool(attrs.get("source_file")) == bool(attrs.get("uploaded_file")):
            raise serializers.ValidationError("Exactly one source_file or uploaded_file is required.")
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


class MobilePrintOrderSerializer(serializers.ModelSerializer):
    items = PrintOrderItemSerializer(many=True, read_only=True)
    status_history = MobilePrintOrderStatusHistorySerializer(many=True, read_only=True)
    pickup_location_detail = PrintPickupLocationSerializer(source="pickup_location", read_only=True)

    class Meta:
        model = PrintOrder
        fields = [
            "id",
            "status",
            "priority",
            "total_price",
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


class DashboardPrintOrderSerializer(serializers.ModelSerializer):
    items = PrintOrderItemSerializer(many=True, read_only=True)
    status_history = DashboardPrintOrderStatusHistorySerializer(many=True, read_only=True)
    user_name = serializers.CharField(source="user.full_name", read_only=True)
    assigned_to_name = serializers.CharField(source="assigned_to.full_name", read_only=True)
    pickup_location_detail = PrintPickupLocationSerializer(source="pickup_location", read_only=True)

    class Meta:
        model = PrintOrder
        fields = [
            "id",
            "user",
            "user_name",
            "status",
            "priority",
            "total_price",
            "currency",
            "pricing_snapshot",
            "pricing_revision",
            "price_calculated_at",
            "pickup_location",
            "pickup_location_detail",
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


class BasePrintRequestSerializer(serializers.Serializer):
    items = PrintOrderItemInputSerializer(many=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request and "items" in self.fields:
            item_serializer = self.fields["items"].child
            item_serializer.context.update({"request": request})
            item_serializer.fields["source_file"].queryset = accessible_files_for_user(request.user).filter(
                is_printable=True
            )

    def to_internal_value(self, data):
        mutable = data.copy()
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
        return PrintPricingService.quote(self.context["request"].user, validated_data["items"])


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
