from rest_framework import serializers

from apps.accounts.models import User

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
    attachment = serializers.FileField(required=False, allow_null=True)

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
    attachment = serializers.FileField(required=False, allow_null=True)

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
        ticket = self.context["ticket"]
        ticket.priority = self.validated_data["priority"]
        ticket.save(update_fields=["priority", "updated_at"])
        return ticket


class SupportTicketAssignSerializer(serializers.Serializer):
    assigned_to = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())

    def save(self, **kwargs):
        ticket = self.context["ticket"]
        ticket.assigned_to = self.validated_data["assigned_to"]
        ticket.save(update_fields=["assigned_to", "updated_at"])
        return ticket
