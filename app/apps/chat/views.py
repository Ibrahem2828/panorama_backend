from __future__ import annotations

import mimetypes
from pathlib import Path

from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils.http import content_disposition_header
from drf_spectacular.utils import OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework import filters, permissions, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.views import APIView

from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.responses import success_response
from apps.common.throttles import ChatMessageRateThrottle, ChatReportRateThrottle, FileTicketRateThrottle
from apps.common.viewsets import StandardReadOnlyModelViewSet
from apps.groups.models import Group

from .models import Message, MessageAttachmentAccessTicket, MessageReport
from .serializers import MessageCreateSerializer, MessageReportSerializer, MessageSerializer
from .services import ChatPermissionService


class GroupMessageViewSet(StandardReadOnlyModelViewSet):
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
        return Message.objects.filter(group=self.get_group(), is_deleted=False).select_related("sender", "reply_to")

    @extend_schema(tags=["Chat"], request=MessageCreateSerializer, responses={201: MessageSerializer})
    def create(self, request, *args, **kwargs):
        self.throttle_classes = [ChatMessageRateThrottle]
        self.check_throttles(request)
        group = self.get_group()
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

    def destroy(self, request, *args, **kwargs):
        group = self.get_group()
        message = get_object_or_404(Message, pk=self.kwargs["pk"], group=group, is_deleted=False)
        if (
            not ChatPermissionService.can_moderate_messages(request.user, group)
            and message.sender_id != request.user.id
        ):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("You cannot delete this message.")
        message.soft_delete(request.user)
        AuditLogService.log(actor=request.user, action=AuditAction.MESSAGE_DELETED, target=message, request=request)
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
        message.soft_delete(request.user)
        AuditLogService.log(actor=request.user, action=AuditAction.MESSAGE_DELETED, target=message, request=request)
        return success_response(message="Message deleted successfully", request=request, code="CHAT_MESSAGE_DELETED")


class GroupMessageReportView(APIView):
    serializer_class = MessageReportSerializer
    throttle_classes = [ChatReportRateThrottle]

    @extend_schema(tags=["Chat"], request=MessageReportSerializer, responses={201: MessageReportSerializer})
    def post(self, request, group_id: int, message_id: int):
        group = get_object_or_404(Group, pk=group_id, is_deleted=False)
        ChatPermissionService.enforce_group_chat_access(request.user, group)
        message = get_object_or_404(Message, pk=message_id, group=group, is_deleted=False)
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
