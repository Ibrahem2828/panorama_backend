from __future__ import annotations

import mimetypes
from pathlib import Path

from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils.http import content_disposition_header
from drf_spectacular.utils import OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework import filters, permissions, serializers, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.views import APIView

from apps.common.idempotency import run_idempotent
from apps.common.responses import success_response
from apps.common.throttles import (
    ChatMessageRateThrottle,
    ChatReportRateThrottle,
    ChatTicketRateThrottle,
    FileTicketRateThrottle,
)
from apps.common.viewsets import StandardExplicitActionViewSet
from apps.groups.models import Group

from .models import GroupChatPreference, Message, MessageAttachmentAccessTicket, MessageReport
from .reactions import ChatReactionService
from .receipts import ChatReceiptService, ChatUnreadService
from .serializers import (
    ChatHandshakeTicketSerializer,
    MessageCreateSerializer,
    MessageReportSerializer,
    MessageSerializer,
)
from .services import ChatHandshakeTicketService, ChatMessageService, ChatPermissionService


class GroupChatHandshakeTicketView(APIView):
    """Create the sole supported credential for a group-chat WebSocket handshake."""

    throttle_classes = [ChatTicketRateThrottle]
    serializer_class = ChatHandshakeTicketSerializer

    @extend_schema(
        tags=["Chat"],
        request=None,
        responses={
            201: ChatHandshakeTicketSerializer,
            401: OpenApiResponse(description="REST authentication is required."),
            403: OpenApiResponse(description="The authenticated user cannot access this group's chat."),
            429: OpenApiResponse(description="Chat-ticket issuance is throttled per authenticated user."),
            503: OpenApiResponse(description="The required ticket cache is temporarily unavailable."),
        },
    )
    def post(self, request, group_id: int):
        group = get_object_or_404(Group, pk=group_id, is_active=True, is_deleted=False)
        ticket = ChatHandshakeTicketService.issue(user=request.user, group=group)
        return success_response(
            data={
                "ticket": ticket.token,
                "expires_at": ticket.expires_at,
                "websocket_path": f"/ws/v1/groups/{group.pk}/chat/",
            },
            message="Short-lived group chat handshake ticket issued",
            status_code=status.HTTP_201_CREATED,
            request=request,
            code="CHAT_HANDSHAKE_TICKET_ISSUED",
        )


class GroupMessageViewSet(StandardExplicitActionViewSet):
    serializer_class = MessageSerializer
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ["created_at"]
    ordering = ["created_at"]

    def get_group(self):
        group = get_object_or_404(Group, pk=self.kwargs["group_id"], is_deleted=False)
        ChatPermissionService.enforce_group_chat_access(self.request.user, group)
        return group

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Message.objects.none()
        queryset = Message.objects.filter(group=self.get_group(), is_deleted=False).select_related(
            "sender", "reply_to", "reply_to__sender"
        )
        # Cursor filters let a client fetch only what it is missing (``after_id``) or scroll back (``before_id``).
        params = self.request.query_params
        for name, lookup in (("after_id", "id__gt"), ("before_id", "id__lt")):
            raw = params.get(name)
            if raw is not None:
                if not raw.isdigit():
                    from rest_framework.exceptions import ValidationError

                    raise ValidationError({name: "Must be a message id."})
                queryset = queryset.filter(**{lookup: int(raw)})
        return queryset

    @extend_schema(tags=["Chat"], request=MessageCreateSerializer, responses={201: MessageSerializer})
    def create(self, request, *args, **kwargs):
        self.throttle_classes = [ChatMessageRateThrottle]
        self.check_throttles(request)
        group = self.get_group()

        def write():
            serializer = MessageCreateSerializer(data=request.data, context={"request": request, "group": group})
            serializer.is_valid(raise_exception=True)
            message = serializer.save()
            return success_response(
                data=MessageSerializer(message, context={"request": request}).data,
                message="Message sent successfully",
                status_code=status.HTTP_201_CREATED,
                request=request,
                code="CHAT_MESSAGE_CREATED",
            )

        return run_idempotent(request, f"chat-message-create:{group.pk}", write)

    def destroy(self, request, *args, **kwargs):
        group = self.get_group()
        message = get_object_or_404(Message, pk=self.kwargs["pk"], group=group, is_deleted=False)
        if (
            not ChatPermissionService.can_moderate_messages(request.user, group)
            and message.sender_id != request.user.id
        ):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("You cannot delete this message.")
        ChatMessageService.delete_message(message, actor=request.user, request=request)
        return success_response(message="Message deleted successfully", request=request, code="CHAT_MESSAGE_DELETED")


class GroupMessageDeleteView(APIView):
    serializer_class = MessageSerializer

    @extend_schema(tags=["Chat"])
    def delete(self, request, group_id: int, message_id: int):
        group = get_object_or_404(Group, pk=group_id, is_deleted=False)
        ChatPermissionService.enforce_group_chat_access(request.user, group)
        message = get_object_or_404(Message, pk=message_id, group=group, is_deleted=False)
        if (
            not ChatPermissionService.can_moderate_messages(request.user, group)
            and message.sender_id != request.user.id
        ):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("You cannot delete this message.")
        ChatMessageService.delete_message(message, actor=request.user, request=request)
        return success_response(message="Message deleted successfully", request=request, code="CHAT_MESSAGE_DELETED")


class GroupMessageReportView(APIView):
    serializer_class = MessageReportSerializer
    throttle_classes = [ChatReportRateThrottle]

    @extend_schema(tags=["Chat"], request=MessageReportSerializer, responses={201: MessageReportSerializer})
    def post(self, request, group_id: int, message_id: int):
        group = get_object_or_404(Group, pk=group_id, is_deleted=False)
        ChatPermissionService.enforce_group_chat_access(request.user, group)
        message = get_object_or_404(Message, pk=message_id, group=group, is_deleted=False)

        def write():
            serializer = MessageReportSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            try:
                report = MessageReport.objects.create(
                    message=message,
                    reported_by=request.user,
                    reason=serializer.validated_data["reason"],
                )
            except IntegrityError as exc:
                from rest_framework.exceptions import ValidationError

                raise ValidationError({"message": "You have already reported this message."}) from exc
            return success_response(
                data=MessageReportSerializer(report).data,
                message="Message reported successfully",
                status_code=status.HTTP_201_CREATED,
                request=request,
                code="CHAT_MESSAGE_REPORTED",
            )

        return run_idempotent(request, f"chat-message-report:{message.pk}", write)


class MessageAttachmentAccessTicketView(APIView):
    throttle_classes = [FileTicketRateThrottle]

    @extend_schema(tags=["Chat"], request=None, responses={201: OpenApiTypes.OBJECT})
    def post(self, request, group_id: int, message_id: int):
        group = get_object_or_404(Group, pk=group_id, is_deleted=False)
        ChatPermissionService.enforce_group_chat_access(request.user, group)
        message = get_object_or_404(Message, pk=message_id, group=group, is_deleted=False)
        if not message.attachment:
            raise Http404("This message does not have an attachment.")
        ticket = MessageAttachmentAccessTicket.issue(message, request.user)
        preview_url = request.build_absolute_uri(f"/api/v1/protected-chat-attachments/{ticket.token}/")
        return success_response(
            data={"preview_url": preview_url, "expires_at": ticket.expires_at, "download_allowed": False},
            message="Protected attachment ticket issued",
            status_code=status.HTTP_201_CREATED,
            request=request,
            code="CHAT_ATTACHMENT_TICKET_ISSUED",
        )


class ProtectedMessageAttachmentStreamView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(tags=["Protected Assets"], responses={200: OpenApiResponse(description="Inline chat attachment")})
    def get(self, request, token):
        with transaction.atomic():
            ticket = (
                MessageAttachmentAccessTicket.objects.select_for_update()
                .select_related("message__group")
                .filter(token=token, is_deleted=False)
                .first()
            )
            if not ticket or not ticket.is_valid or ticket.requested_by_id != request.user.id:
                raise Http404("The protected attachment link is invalid or expired.")
            ChatPermissionService.enforce_group_chat_access(request.user, ticket.message.group)
            attachment = ticket.message.attachment
            ticket.use_count += 1
            ticket.save(update_fields=["use_count", "updated_at"])
        filename = Path(attachment.name).name
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        response = FileResponse(attachment.open("rb"), content_type=content_type)
        content_disposition = content_disposition_header(False, filename)
        if content_disposition:
            response["Content-Disposition"] = content_disposition
        response["Cache-Control"] = "private, no-store, max-age=0"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = (
            "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"
        )
        return response


class ReceiptSerializer(serializers.Serializer):
    delivered_up_to = serializers.IntegerField(min_value=0, required=False, default=0)
    read_up_to = serializers.IntegerField(min_value=0, required=False, default=0)


class GroupReceiptView(APIView):
    """Tell the server how far this member's device has received / read the chat (drives everyone's ticks)."""

    @extend_schema(tags=["Chat"], request=ReceiptSerializer, responses={200: ReceiptSerializer})
    def post(self, request, group_id: int):
        group = get_object_or_404(Group, pk=group_id, is_active=True, is_deleted=False)
        serializer = ReceiptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        state = ChatReceiptService.update(
            group=group,
            user=request.user,
            delivered_up_to=serializer.validated_data["delivered_up_to"],
            read_up_to=serializer.validated_data["read_up_to"],
        )
        return success_response(
            data={"delivered_up_to": state.delivered_up_to, "read_up_to": state.read_up_to},
            message="Receipts updated",
            request=request,
            code="CHAT_RECEIPTS_UPDATED",
        )


class ReactionSerializer(serializers.Serializer):
    emoji = serializers.CharField(allow_null=True, max_length=16)


class MessageReactionView(APIView):
    """PUT sets (or replaces) the member's single reaction, DELETE removes it."""

    @extend_schema(tags=["Chat"], request=ReactionSerializer, responses={200: OpenApiTypes.OBJECT})
    def put(self, request, group_id: int, message_id: int):
        serializer = ReactionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self._react(request, group_id, message_id, serializer.validated_data["emoji"])

    @extend_schema(tags=["Chat"], responses={200: OpenApiTypes.OBJECT})
    def delete(self, request, group_id: int, message_id: int):
        return self._react(request, group_id, message_id, None)

    @staticmethod
    def _react(request, group_id: int, message_id: int, emoji):
        message = get_object_or_404(
            Message.objects.select_related("group"), pk=message_id, group_id=group_id, is_deleted=False
        )
        counts = ChatReactionService.react(message=message, user=request.user, emoji=emoji)
        return success_response(
            data={"message_id": message.pk, "my_reaction": emoji, "counts": counts},
            message="Reaction saved",
            request=request,
            code="CHAT_REACTION_SAVED",
        )


class ChatPreferenceSerializer(serializers.Serializer):
    is_muted = serializers.BooleanField()


class GroupChatPreferenceView(APIView):
    @extend_schema(tags=["Chat"], responses={200: ChatPreferenceSerializer})
    def get(self, request, group_id: int):
        group = get_object_or_404(Group, pk=group_id, is_deleted=False)
        ChatPermissionService.enforce_group_chat_access(request.user, group)
        muted = GroupChatPreference.objects.filter(group=group, user=request.user, is_muted=True).exists()
        return success_response(data={"is_muted": muted}, message="Chat preferences", request=request)

    @extend_schema(tags=["Chat"], request=ChatPreferenceSerializer, responses={200: ChatPreferenceSerializer})
    def put(self, request, group_id: int):
        group = get_object_or_404(Group, pk=group_id, is_deleted=False)
        ChatPermissionService.enforce_group_chat_access(request.user, group)
        serializer = ChatPreferenceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        GroupChatPreference.objects.update_or_create(
            group=group, user=request.user, defaults={"is_muted": serializer.validated_data["is_muted"]}
        )
        return success_response(
            data={"is_muted": serializer.validated_data["is_muted"]},
            message="Chat preferences saved",
            request=request,
            code="CHAT_PREFERENCES_SAVED",
        )


class ChatUnreadView(APIView):
    """Unread message count per chat the user belongs to (for list badges and the app icon)."""

    @extend_schema(tags=["Chat"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return success_response(
            data={"groups": ChatUnreadService.counts(request.user)}, message="Unread chats", request=request
        )
