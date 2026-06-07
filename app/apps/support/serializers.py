from rest_framework import serializers
from django.db import transaction

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.upload_validation import validate_document_upload

from .models import SupportTicket, SupportTicketMessage, SupportTicketPriority, SupportTicketStatus
from .services import SupportTicketService


class SupportTicketMessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.CharField(source="sender.full_name", read_only=True)

    class Meta:
        model = SupportTicketMessage
        fields = ["id", "ticket", "sender", "sender_name", "message", "attachment", "created_at"]
        read_only_fields = ["id", "ticket", "sender", "created_at"]


class SupportTicketSerializer(serializers.ModelSerializer):
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
            "messages",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "user", "status", "priority", "assigned_to", "closed_at", "created_at", "updated_at"]


class SupportTicketCreateSerializer(serializers.Serializer):
    category = serializers.ChoiceField(choices=SupportTicket._meta.get_field("category").choices)
    subject = serializers.CharField(max_length=255)
    message = serializers.CharField()
    attachment = serializers.FileField(required=False, allow_null=True, validators=[validate_document_upload])

    def validate_message(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError("Message cannot be empty.")
        return value.strip()

    def save(self, **kwargs):
        return SupportTicketService.create_ticket(
            self.context["request"].user,
            self.validated_data["category"],
            self.validated_data["subject"],
            self.validated_data["message"],
            self.validated_data.get("attachment"),
        )


class SupportTicketAddMessageSerializer(serializers.Serializer):
    message = serializers.CharField()
    attachment = serializers.FileField(required=False, allow_null=True, validators=[validate_document_upload])

    def validate_message(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError("Message cannot be empty.")
        return value.strip()

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
        return SupportTicketService.update_status(self.context["ticket"], self.validated_data["status"], self.context["request"].user)


class SupportTicketPrioritySerializer(serializers.Serializer):
    priority = serializers.ChoiceField(choices=SupportTicketPriority.choices)

    def save(self, **kwargs):
        with transaction.atomic():
            ticket = SupportTicket.objects.select_for_update().get(pk=self.context["ticket"].pk, is_deleted=False)
            old_priority = ticket.priority
            ticket.priority = self.validated_data["priority"]
            ticket.save(update_fields=["priority", "updated_at"])
            AuditLogService.log(
                actor=self.context.get("request").user if self.context.get("request") else None,
                action=AuditAction.SUPPORT_TICKET_PRIORITY_CHANGED,
                target=ticket,
                old_value={"priority": old_priority},
                new_value={"priority": ticket.priority},
                request=self.context.get("request"),
            )
        return ticket


class SupportTicketAssignSerializer(serializers.Serializer):
    assigned_to = serializers.PrimaryKeyRelatedField(queryset=User.objects.filter(role__in=[UserRole.ADMIN, UserRole.IT_SUPPORT]))

    def save(self, **kwargs):
        with transaction.atomic():
            ticket = SupportTicket.objects.select_for_update().get(pk=self.context["ticket"].pk, is_deleted=False)
            old_assigned_to = ticket.assigned_to_id
            ticket.assigned_to = self.validated_data["assigned_to"]
            ticket.save(update_fields=["assigned_to", "updated_at"])
            AuditLogService.log(
                actor=self.context.get("request").user if self.context.get("request") else None,
                action=AuditAction.SUPPORT_TICKET_ASSIGNED,
                target=ticket,
                old_value={"assigned_to": old_assigned_to},
                new_value={"assigned_to": ticket.assigned_to_id},
                request=self.context.get("request"),
            )
        return ticket
