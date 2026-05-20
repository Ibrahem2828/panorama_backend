from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import Message, MessageReport, MessageType
from .services import ChatMessageService


class MessageSenderSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    full_name = serializers.CharField()


class MessageSerializer(serializers.ModelSerializer):
    sender_detail = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            "id",
            "group",
            "sender",
            "sender_detail",
            "content",
            "message_type",
            "attachment",
            "reply_to",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "group", "sender", "is_deleted", "deleted_at", "created_at", "updated_at"]

    @extend_schema_field(MessageSenderSerializer)
    def get_sender_detail(self, obj):
        return {"id": obj.sender_id, "full_name": obj.sender.full_name}


class MessageCreateSerializer(serializers.Serializer):
    content = serializers.CharField(required=False, allow_blank=True)
    message_type = serializers.ChoiceField(choices=MessageType.choices, default=MessageType.TEXT)
    attachment = serializers.FileField(required=False, allow_null=True)
    reply_to = serializers.PrimaryKeyRelatedField(queryset=Message.objects.all(), required=False, allow_null=True)

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
    class Meta:
        model = MessageReport
        fields = ["id", "message", "reported_by", "reason", "status", "created_at"]
        read_only_fields = ["id", "message", "reported_by", "status", "created_at"]
