from __future__ import annotations

from rest_framework import serializers

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.common.file_validation import validate_document_upload

from .models import SupportTicket, SupportTicketMessage, SupportTicketPriority, SupportTicketStatus
from .services import SupportTicketService


class SupportTicketMessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.CharField(source="sender.full_name", read_only=True)
    has_attachment = serializers.SerializerMethodField()
    attachment_preview_endpoint = serializers.SerializerMethodField()

    class Meta:
        model = SupportTicketMessage
        fields = [
            "id",
            "ticket",
            "sender",
            "sender_name",
            "message",
            "has_attachment",
            "attachment_preview_endpoint",
            "created_at",
        ]
        read_only_fields = fields

    def get_has_attachment(self, obj) -> bool:
        return bool(obj.attachment)

    def get_attachment_preview_endpoint(self, obj) -> str | None:
        return f"/api/v1/support/messages/{obj.id}/attachment-ticket/" if obj.attachment else None


class MobileSupportTicketSerializer(serializers.ModelSerializer):
    messages = SupportTicketMessageSerializer(many=True, read_only=True)

    class Meta:
        model = SupportTicket
        fields = [
            "id",
            "category",
            "subject",
            "status",
            "closed_at",
            "last_response_at",
            "messages",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class DashboardSupportTicketSerializer(serializers.ModelSerializer):
    messages = SupportTicketMessageSerializer(many=True, read_only=True)
    user_name = serializers.CharField(source="user.full_name", read_only=True)
    assigned_to_name = serializers.CharField(source="assigned_to.full_name", read_only=True)

    class Meta:
        model = SupportTicket
        fields = [
            "id",
            "user",
            "user_name",
            "category",
            "subject",
            "status",
            "priority",
            "assigned_to",
            "assigned_to_name",
            "closed_at",
            "last_response_at",
            "messages",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class SupportTicketCreateSerializer(serializers.Serializer):
    category = serializers.ChoiceField(choices=SupportTicket._meta.get_field("category").choices)
    subject = serializers.CharField(max_length=255, trim_whitespace=True)
    message = serializers.CharField(trim_whitespace=True)
    attachment = serializers.FileField(required=False, allow_null=True, write_only=True)

    def validate_subject(self, value):
        if len(value.strip()) < 4:
            raise serializers.ValidationError("Subject must contain at least 4 characters.")
        return value.strip()

    def validate_message(self, value):
        if len(value.strip()) < 10:
            raise serializers.ValidationError("Message must contain at least 10 characters.")
        return value.strip()

    def validate_attachment(self, value):
        return validate_document_upload(value, "attachment")

    def save(self, **kwargs):
        return SupportTicketService.create_ticket(
            self.context["request"].user,
            self.validated_data["category"],
            self.validated_data["subject"],
            self.validated_data["message"],
            self.validated_data.get("attachment"),
            request=self.context["request"],
        )


class SupportTicketAddMessageSerializer(serializers.Serializer):
    message = serializers.CharField(trim_whitespace=True)
    attachment = serializers.FileField(required=False, allow_null=True, write_only=True)

    def validate_message(self, value):
        if len(value.strip()) < 2:
            raise serializers.ValidationError("Message is too short.")
        return value.strip()

    def validate_attachment(self, value):
        return validate_document_upload(value, "attachment")

    def save(self, **kwargs):
        return SupportTicketService.add_message(
            self.context["ticket"],
            self.context["request"].user,
            self.validated_data["message"],
            self.validated_data.get("attachment"),
        )


class SupportTicketStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=SupportTicketStatus.choices)

    def save(self, **kwargs):
        return SupportTicketService.update_status(
            self.context["ticket"],
            self.validated_data["status"],
            self.context["request"].user,
            request=self.context["request"],
        )


class SupportTicketPrioritySerializer(serializers.Serializer):
    priority = serializers.ChoiceField(choices=SupportTicketPriority.choices)


class SupportTicketAssignSerializer(serializers.Serializer):
    assigned_to = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(
            role__in=[UserRole.IT_SUPPORT, UserRole.ADMIN, UserRole.SUPPORT_STAFF], is_active=True, is_deleted=False
        ),
        allow_null=True,
    )
