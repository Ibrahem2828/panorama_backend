import json

from rest_framework import serializers

from apps.files.models import FileResource

from .models import PrintOrder, PrintOrderItem, PrintOrderStatus, PrintOrderStatusHistory
from .services import PrintOrderService, PrintStatusService


class PrintOrderItemSerializer(serializers.ModelSerializer):
    source_file = serializers.PrimaryKeyRelatedField(queryset=FileResource.objects.all(), required=False, allow_null=True)

    class Meta:
        model = PrintOrderItem
        fields = [
            "id",
            "source_file",
            "uploaded_file",
            "original_file_name",
            "file_type",
            "file_size",
            "pages_count",
            "copies",
            "color_mode",
            "paper_size",
            "sides",
            "binding",
            "price",
            "created_at",
        ]
        read_only_fields = ["id", "original_file_name", "file_type", "file_size", "created_at"]


class PrintOrderStatusHistorySerializer(serializers.ModelSerializer):
    changed_by_name = serializers.CharField(source="changed_by.full_name", read_only=True)

    class Meta:
        model = PrintOrderStatusHistory
        fields = ["id", "old_status", "new_status", "changed_by", "changed_by_name", "note", "created_at"]
        read_only_fields = fields


class PrintOrderSerializer(serializers.ModelSerializer):
    items = PrintOrderItemSerializer(many=True, read_only=True)
    status_history = PrintOrderStatusHistorySerializer(many=True, read_only=True)
    user_name = serializers.CharField(source="user.full_name", read_only=True)
    assigned_to_name = serializers.CharField(source="assigned_to.full_name", read_only=True)

    class Meta:
        model = PrintOrder
        fields = [
            "id",
            "user",
            "user_name",
            "status",
            "priority",
            "total_price",
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


class PrintOrderCreateSerializer(serializers.Serializer):
    user_notes = serializers.CharField(required=False, allow_blank=True)
    items = PrintOrderItemSerializer(many=True, required=False)

    def to_internal_value(self, data):
        mutable = data.copy()
        if isinstance(mutable.get("items"), str):
            mutable["items"] = json.loads(mutable["items"])
        return super().to_internal_value(mutable)

    def validate(self, attrs):
        if not attrs.get("items"):
            uploaded_file = self.initial_data.get("uploaded_file")
            if uploaded_file:
                attrs["items"] = [{"uploaded_file": uploaded_file}]
        if not attrs.get("items"):
            raise serializers.ValidationError({"items": "At least one print item is required."})
        return attrs

    def save(self, **kwargs):
        return PrintOrderService.create_order(
            self.context["request"].user,
            self.validated_data["items"],
            self.validated_data.get("user_notes", ""),
        )


class PrintStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=PrintOrderStatus.choices)
    note = serializers.CharField(required=False, allow_blank=True)
    rejected_reason = serializers.CharField(required=False, allow_blank=True)

    def save(self, **kwargs):
        return PrintStatusService.change_status(
            self.context["order"],
            self.validated_data["status"],
            self.context["request"].user,
            note=self.validated_data.get("note", ""),
            rejected_reason=self.validated_data.get("rejected_reason", ""),
        )


class PrintOrderAssignSerializer(serializers.Serializer):
    assigned_to = serializers.PrimaryKeyRelatedField(queryset=PrintOrder._meta.get_field("user").remote_field.model.objects.all())

    def save(self, **kwargs):
        order = self.context["order"]
        order.assigned_to = self.validated_data["assigned_to"]
        order.save(update_fields=["assigned_to", "updated_at"])
        return order


class PrintOrderNoteSerializer(serializers.Serializer):
    internal_notes = serializers.CharField()

    def save(self, **kwargs):
        order = self.context["order"]
        order.internal_notes = self.validated_data["internal_notes"]
        order.save(update_fields=["internal_notes", "updated_at"])
        return order
