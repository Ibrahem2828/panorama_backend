from __future__ import annotations

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.common.file_validation import validate_document_upload

from .models import Message, MessageReport, MessageType
from .services import ChatMessageService


class MessageSenderSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    full_name = serializers.CharField()


class MessageSerializer(serializers.ModelSerializer):
    sender_detail = serializers.SerializerMethodField()
    has_attachment = serializers.SerializerMethodField()
    attachment_ticket_endpoint = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            "id",
            "group",
            "sender",
            "sender_detail",
            "content",
            "message_type",
            "has_attachment",
            "attachment_ticket_endpoint",
            "reply_to",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    @extend_schema_field(MessageSenderSerializer)
    def get_sender_detail(self, obj):
        return {"id": obj.sender_id, "full_name": obj.sender.full_name}

    def get_has_attachment(self, obj) -> bool:
        return bool(obj.attachment)

    def get_attachment_ticket_endpoint(self, obj) -> str | None:
        return f"/api/v1/groups/{obj.group_id}/messages/{obj.pk}/attachment-ticket/" if obj.attachment else None


class MessageCreateSerializer(serializers.Serializer):
    content = serializers.CharField(required=False, allow_blank=True, max_length=4000)
    message_type = serializers.ChoiceField(choices=MessageType.choices, default=MessageType.TEXT)
    attachment = serializers.FileField(required=False, allow_null=True, write_only=True)
    reply_to = serializers.PrimaryKeyRelatedField(queryset=Message.objects.none(), required=False, allow_null=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        group = self.context.get("group")
        if group:
            self.fields["reply_to"].queryset = Message.objects.filter(group=group, is_deleted=False)

    def validate_attachment(self, value):
        return validate_document_upload(value, "attachment") if value else value

    def validate(self, attrs):
        message_type = attrs.get("message_type", MessageType.TEXT)
        content = str(attrs.get("content", "")).strip()
        attachment = attrs.get("attachment")
        if message_type == MessageType.TEXT and not content:
            raise serializers.ValidationError({"content": "Content is required for text messages."})
        if message_type in {MessageType.IMAGE, MessageType.FILE} and not attachment:
            raise serializers.ValidationError({"attachment": "Attachment is required for this message type."})
        if message_type == MessageType.SYSTEM:
            raise serializers.ValidationError({"message_type": "System messages cannot be created by clients."})
        return attrs

    def save(self, **kwargs):
        return ChatMessageService.create_message(
            group=self.context["group"],
            sender=self.context["request"].user,
            content=self.validated_data.get("content", ""),
            message_type=self.validated_data.get("message_type", MessageType.TEXT),
            attachment=self.validated_data.get("attachment"),
            reply_to=self.validated_data.get("reply_to"),
        )


class MessageReportSerializer(serializers.ModelSerializer):
    reason = serializers.CharField(max_length=2000)

    class Meta:
        model = MessageReport
        fields = ["id", "message", "reported_by", "reason", "status", "created_at"]
        read_only_fields = ["id", "message", "reported_by", "status", "created_at"]
