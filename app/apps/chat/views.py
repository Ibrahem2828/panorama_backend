from __future__ import annotations

import mimetypes
from datetime import timedelta
from pathlib import Path

from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
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
from apps.groups.models import Group, GroupMembershipStatus

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
            from apps.notifications.staff import notify_staff

            notify_staff(
                "chat_report",
                "بلاغ جديد على رسالة",
                f"بلاغ في مجموعة «{group.name}»: {report.reason[:80]}",
                object_id=report.pk,
                actor_id=request.user.pk,
            )
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


MUTE_DURATIONS = {"8h": timedelta(hours=8), "1w": timedelta(weeks=1), "forever": None}


class ChatPreferenceSerializer(serializers.Serializer):
    is_muted = serializers.BooleanField()
    mute_for = serializers.ChoiceField(choices=list(MUTE_DURATIONS), required=False)


class ChatPreferenceOutSerializer(serializers.Serializer):
    is_muted = serializers.BooleanField()
    muted_until = serializers.DateTimeField(allow_null=True)


class ChatPreferenceListItemSerializer(serializers.Serializer):
    group_id = serializers.IntegerField()
    name = serializers.CharField()
    kind = serializers.ChoiceField(choices=["subject", "community"])
    is_muted = serializers.BooleanField()
    muted_until = serializers.DateTimeField(allow_null=True)


def _mute_payload(preference: GroupChatPreference | None, now=None) -> dict:
    muted = preference is not None and preference.effective_muted(now)
    return {"is_muted": muted, "muted_until": preference.muted_until if muted and preference else None}


class GroupChatPreferenceView(APIView):
    @extend_schema(tags=["Chat"], responses={200: ChatPreferenceOutSerializer})
    def get(self, request, group_id: int):
        group = get_object_or_404(Group, pk=group_id, is_deleted=False)
        ChatPermissionService.enforce_group_chat_access(request.user, group)
        preference = GroupChatPreference.objects.filter(group=group, user=request.user).first()
        now = timezone.now()
        if preference is not None and preference.is_muted and not preference.effective_muted(now):
            # lazily normalise an expired timed mute
            preference.is_muted = False
            preference.muted_until = None
            preference.save(update_fields=["is_muted", "muted_until", "updated_at"])
        return success_response(data=_mute_payload(preference, now), message="Chat preferences", request=request)

    @extend_schema(tags=["Chat"], request=ChatPreferenceSerializer, responses={200: ChatPreferenceOutSerializer})
    def put(self, request, group_id: int):
        group = get_object_or_404(Group, pk=group_id, is_deleted=False)
        ChatPermissionService.enforce_group_chat_access(request.user, group)
        serializer = ChatPreferenceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        is_muted = serializer.validated_data["is_muted"]
        duration = MUTE_DURATIONS[serializer.validated_data.get("mute_for", "forever")] if is_muted else None
        muted_until = timezone.now() + duration if duration is not None else None
        preference, _created = GroupChatPreference.objects.update_or_create(
            group=group, user=request.user, defaults={"is_muted": is_muted, "muted_until": muted_until}
        )
        return success_response(
            data=_mute_payload(preference),
            message="Chat preferences saved",
            request=request,
            code="CHAT_PREFERENCES_SAVED",
        )


class ChatPreferenceListView(APIView):
    """Every chat the member belongs to with its mute state, for one management screen in the app."""

    @extend_schema(tags=["Chat"], responses={200: ChatPreferenceListItemSerializer(many=True)})
    def get(self, request):
        now = timezone.now()
        memberships = (
            request.user.group_memberships.filter(
                status=GroupMembershipStatus.APPROVED, group__is_deleted=False, group__is_active=True
            )
            .select_related("group")
            .order_by("group__name", "group_id")
        )
        preferences = {
            item.group_id: item for item in GroupChatPreference.objects.filter(user=request.user, is_muted=True)
        }
        items = []
        for membership in memberships:
            group = membership.group
            payload = _mute_payload(preferences.get(group.pk), now)
            items.append(
                {
                    "group_id": group.pk,
                    "name": group.name,
                    "kind": "subject" if group.subject_id else "community",
                    **payload,
                }
            )
        return success_response(data=items, message="Chat preferences", request=request)


class ChatUnreadView(APIView):
    """Unread message count per chat the user belongs to (for list badges and the app icon)."""

    @extend_schema(tags=["Chat"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return success_response(
            data={"groups": ChatUnreadService.counts(request.user)}, message="Unread chats", request=request
        )
