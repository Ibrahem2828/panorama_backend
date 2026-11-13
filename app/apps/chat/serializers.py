from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.permissions import Capability, PermissionService

from .media import ChatMediaError, inspect_attachment, parse_waveform
from .models import Message, MessageReport, MessageType
from .reactions import ChatReactionService
from .receipts import ChatReceiptService
from .services import ChatMessageService
from .titles import chat_title

ATTACHMENT_TYPES = {MessageType.IMAGE, MessageType.FILE, MessageType.VOICE, MessageType.AUDIO}


class MessageSenderSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    #: Same as ``id``; lets clients open the sender's profile without guessing which field is the account.
    user_id = serializers.IntegerField()
    #: For an administrator this is their position (e.g. "بانوراما أدمن"), never their name.
    full_name = serializers.CharField()
    title = serializers.CharField(allow_null=True)
    is_staff = serializers.BooleanField()
    #: The sender's own "job title" (المسمى الوظيفي); empty when they set none.
    job_title = serializers.CharField(allow_blank=True)
    #: Only sent to staff who manage groups: the administrator's real name behind the position.
    real_name = serializers.CharField(required=False)


class ChatHandshakeTicketSerializer(serializers.Serializer):
    """Response contract for a short-lived group-chat WebSocket handshake."""

    ticket = serializers.CharField(read_only=True)
    expires_at = serializers.DateTimeField(read_only=True)
    websocket_path = serializers.CharField(read_only=True)


class MessageListSerializer(serializers.ListSerializer):
    """Looks up reactions, ticks and the viewer's rights once for the whole page instead of once per message."""

    def to_representation(self, data):
        items = list(data)
        cast(MessageSerializer, self.child).prepare(items)
        return super().to_representation(items)


class MessageSerializer(serializers.ModelSerializer):
    sender_detail = serializers.SerializerMethodField()
    has_attachment = serializers.SerializerMethodField()
    attachment_ticket_endpoint = serializers.SerializerMethodField()
    attachment_meta = serializers.SerializerMethodField()
    reply_to_detail = serializers.SerializerMethodField()
    reactions = serializers.SerializerMethodField()
    my_reaction = serializers.SerializerMethodField()
    delivery = serializers.SerializerMethodField()

    class Meta:
        model = Message
        list_serializer_class = MessageListSerializer
        fields = [
            "id",
            "group",
            "sender",
            "sender_detail",
            "content",
            "message_type",
            "has_attachment",
            "attachment_ticket_endpoint",
            "attachment_meta",
            "reply_to",
            "reply_to_detail",
            "reactions",
            "my_reaction",
            "delivery",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    # -- per-page lookups ------------------------------------------------------------------------------------------

    def _viewer(self):
        request = self.context.get("request")
        user = getattr(request, "user", None) or self.context.get("viewer")
        return user if user is not None and getattr(user, "is_authenticated", False) else None

    def prepare(self, messages: list[Message]) -> None:
        ids = [message.pk for message in messages]
        viewer = self._viewer()
        state = {
            "counts": ChatReactionService.counts(ids) if ids else {},
            "mine": ChatReactionService.mine(ids, viewer) if ids and viewer else {},
            "reveal": bool(viewer and PermissionService.has(viewer, Capability.GROUPS_MANAGE)),
            "cursors": None,
            "recipients": 0,
            "ids": set(ids),
        }
        if viewer and any(message.sender_id == viewer.pk for message in messages):
            group = messages[0].group
            state["cursors"] = ChatReceiptService.cursors(group)
            state["recipients"] = ChatReceiptService.member_count(group)
        self._state = state

    def _lookups(self, obj) -> dict:
        state = getattr(self, "_state", None)
        if state is None or obj.pk not in state["ids"]:
            self.prepare([obj])
        return self._state

    # -- fields ----------------------------------------------------------------------------------------------------

    @staticmethod
    def _sender(user, reveal: bool) -> dict:
        title = chat_title(user)
        data = {
            "id": user.pk,
            "user_id": user.pk,
            "full_name": title or user.full_name,
            "title": title,
            "is_staff": bool(title),
            "job_title": user.job_title or "",
        }
        if title and reveal:
            data["real_name"] = user.full_name
        return data

    @extend_schema_field(MessageSenderSerializer)
    def get_sender_detail(self, obj):
        return self._sender(obj.sender, self._lookups(obj)["reveal"])

    def get_has_attachment(self, obj) -> bool:
        return bool(obj.attachment)

    def get_attachment_ticket_endpoint(self, obj) -> str | None:
        return f"/api/v1/groups/{obj.group_id}/messages/{obj.pk}/attachment-ticket/" if obj.attachment else None

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_attachment_meta(self, obj):
        if not obj.attachment:
            return None
        try:
            size = obj.attachment.size
        except Exception:
            size = None
        return {
            "name": obj.attachment_name or Path(obj.attachment.name).name,
            "size": obj.attachment_size if obj.attachment_size is not None else size,
            "mime": obj.attachment_mime or None,
            "kind": obj.message_type,
            "duration_ms": obj.attachment_duration_ms,
            "waveform": obj.attachment_waveform,
            "width": obj.attachment_width,
            "height": obj.attachment_height,
            "has_thumbnail": bool(obj.attachment_thumb) or obj.attachment_is_image,
            "etag": obj.attachment_sha256 or None,
        }

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_reply_to_detail(self, obj):
        target = obj.reply_to
        if target is None:
            return None
        if target.is_deleted:
            return {"id": target.pk, "is_deleted": True}
        label = chat_title(target.sender) or target.sender.full_name
        previews = {
            MessageType.IMAGE: "صورة",
            MessageType.FILE: "ملف",
            MessageType.VOICE: "رسالة صوتية",
            MessageType.AUDIO: "ملف صوتي",
        }
        preview = previews.get(target.message_type) or " ".join(target.content.split())[:80]
        return {"id": target.pk, "is_deleted": False, "sender_name": label, "preview": preview}

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_reactions(self, obj):
        counts = self._lookups(obj)["counts"].get(obj.pk, {})
        return [{"emoji": emoji, "count": count} for emoji, count in sorted(counts.items(), key=lambda item: -item[1])]

    def get_my_reaction(self, obj) -> str | None:
        return self._lookups(obj)["mine"].get(obj.pk)

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_delivery(self, obj):
        viewer = self._viewer()
        state = self._lookups(obj)
        if viewer is None or obj.sender_id != viewer.pk or state["cursors"] is None:
            return None
        return ChatReceiptService.delivery_for(obj.pk, obj.sender_id, state["cursors"], state["recipients"]).as_dict()


@extend_schema_field(
    {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 255}, "maxItems": 128, "nullable": True}
)
class WaveformField(serializers.Field):
    """Optional list of 0-255 bar heights. In multipart send a JSON array string (``"[3,40,...]"``)."""

    def get_value(self, dictionary):
        if hasattr(dictionary, "getlist"):
            values = dictionary.getlist(self.field_name)
            return values if len(values) > 1 else (values[0] if values else serializers.empty)
        return dictionary.get(self.field_name, serializers.empty)

    def to_internal_value(self, data):
        try:
            return parse_waveform(data)
        except ChatMediaError as exc:
            cast(
                Any, self.parent
            )._media_error = exc  # surfaced with its own code by MessageCreateSerializer.run_validation
            raise

    def to_representation(self, value):
        return value


class MessageCreateSerializer(serializers.Serializer):
    content = serializers.CharField(required=False, allow_blank=True, max_length=4000)
    message_type = serializers.ChoiceField(choices=MessageType.choices, default=MessageType.TEXT)
    attachment = serializers.FileField(required=False, allow_null=True, write_only=True)
    reply_to = serializers.PrimaryKeyRelatedField(queryset=Message.objects.none(), required=False, allow_null=True)
    #: Client's own measured length of a voice note. Only a hint: the server measures the file itself.
    duration_ms = serializers.IntegerField(required=False, allow_null=True, min_value=0, max_value=86_400_000)
    #: Optional bar heights (<= 128 ints, 0-255) for the voice/audio UI.
    waveform = WaveformField(required=False, allow_null=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        group = self.context.get("group")
        if group:
            reply_to = cast(serializers.PrimaryKeyRelatedField, self.fields["reply_to"])
            reply_to.queryset = Message.objects.filter(group=group, is_deleted=False)

    def validate(self, attrs):
        message_type = attrs.get("message_type", MessageType.TEXT)
        content = str(attrs.get("content", "")).strip()
        attachment = attrs.get("attachment")
        if message_type == MessageType.TEXT and not content:
            raise serializers.ValidationError({"content": "Content is required for text messages."})
        if message_type in ATTACHMENT_TYPES and not attachment:
            raise serializers.ValidationError({"attachment": "Attachment is required for this message type."})
        if message_type == MessageType.SYSTEM:
            raise serializers.ValidationError({"message_type": "System messages cannot be created by clients."})
        if attachment and message_type not in ATTACHMENT_TYPES:
            raise serializers.ValidationError({"attachment": "Text messages cannot carry an attachment."})
        if attachment:
            try:
                attrs["media"] = inspect_attachment(
                    attachment,
                    message_type,
                    duration_ms=attrs.get("duration_ms"),
                    waveform=attrs.get("waveform"),
                )
            except ChatMediaError as exc:
                self._media_error = exc
                raise
        return attrs

    _media_error: ChatMediaError | None = None

    def is_valid(self, *, raise_exception=False):
        # DRF re-wraps validation errors; keep the media error so its stable ``code`` reaches the client.
        self._media_error = None
        try:
            return super().is_valid(raise_exception=raise_exception)
        except serializers.ValidationError:
            if self._media_error is not None:
                raise self._media_error from None
            raise

    def save(self, **kwargs):
        return ChatMessageService.create_and_announce(
            group=self.context["group"],
            sender=self.context["request"].user,
            content=self.validated_data.get("content", ""),
            message_type=self.validated_data.get("message_type", MessageType.TEXT),
            attachment=self.validated_data.get("attachment"),
            reply_to=self.validated_data.get("reply_to"),
            media=self.validated_data.get("media"),
        )


class MessageReportSerializer(serializers.ModelSerializer):
    reason = serializers.CharField(max_length=2000)

    class Meta:
        model = MessageReport
        fields = ["id", "message", "reported_by", "reason", "status", "created_at"]
        read_only_fields = ["id", "message", "reported_by", "status", "created_at"]
