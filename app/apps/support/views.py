from __future__ import annotations

from pathlib import Path

from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiTypes, extend_schema
from rest_framework import filters, permissions, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.views import APIView

from apps.accounts.permissions import CanManageSupport
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.responses import success_response
from apps.common.throttles import FileTicketRateThrottle, SupportMessageRateThrottle, SupportTicketRateThrottle
from apps.common.viewsets import StandardReadOnlyModelViewSet

from .models import SupportAttachmentAccessTicket, SupportTicket, SupportTicketMessage
from .serializers import (
    DashboardSupportTicketSerializer,
    MobileSupportTicketSerializer,
    SupportTicketAddMessageSerializer,
    SupportTicketAssignSerializer,
    SupportTicketCreateSerializer,
    SupportTicketPrioritySerializer,
    SupportTicketStatusSerializer,
)
from .services import SupportTicketService


def _serialize(ticket, request, dashboard=False):
    ticket = SupportTicket.objects.select_related("user", "assigned_to").prefetch_related("messages__sender").get(pk=ticket.pk)
    serializer_class = DashboardSupportTicketSerializer if dashboard else MobileSupportTicketSerializer
    return serializer_class(ticket, context={"request": request}).data


class SupportTicketCreateView(APIView):
    throttle_classes = [SupportTicketRateThrottle]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    serializer_class = SupportTicketCreateSerializer

    @extend_schema(tags=["Support"], request=SupportTicketCreateSerializer, responses={201: MobileSupportTicketSerializer})
    def post(self, request):
        serializer = self.serializer_class(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        ticket = serializer.save()
        return success_response(
            data=_serialize(ticket, request), message="Support ticket created successfully",
            status_code=status.HTTP_201_CREATED, request=request, code="SUPPORT_TICKET_CREATED",
        )


class MySupportTicketViewSet(StandardReadOnlyModelViewSet):
    serializer_class = MobileSupportTicketSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["status", "priority", "category"]
    ordering_fields = ["created_at", "status", "priority", "last_response_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return SupportTicket.objects.none()
        return SupportTicket.objects.filter(user=self.request.user, is_deleted=False).select_related("assigned_to").prefetch_related("messages__sender")


class SupportTicketMessageView(APIView):
    throttle_classes = [SupportMessageRateThrottle]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    serializer_class = SupportTicketAddMessageSerializer

    @extend_schema(tags=["Support"], request=SupportTicketAddMessageSerializer, responses={201: MobileSupportTicketSerializer})
    def post(self, request, pk: int):
        ticket = get_object_or_404(SupportTicket, pk=pk, user=request.user, is_deleted=False)
        serializer = self.serializer_class(data=request.data, context={"request": request, "ticket": ticket})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return success_response(
            data=_serialize(ticket, request), message="Support ticket message added",
            status_code=status.HTTP_201_CREATED, request=request, code="SUPPORT_MESSAGE_ADDED",
        )


class SupportAttachmentTicketView(APIView):
    throttle_classes = [FileTicketRateThrottle]

    @extend_schema(tags=["Support"], request=None, responses={200: OpenApiTypes.OBJECT})
    def post(self, request, pk: int):
        message = get_object_or_404(SupportTicketMessage.objects.select_related("ticket"), pk=pk, is_deleted=False)
        ticket = SupportTicketService.issue_attachment_ticket(message, request.user)
        return success_response(
            data={"preview_url": f"/api/v1/support/attachments/{ticket.token}/", "expires_at": ticket.expires_at},
            request=request, code="SUPPORT_ATTACHMENT_TICKET_ISSUED",
        )


class SupportAttachmentStreamView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    @extend_schema(auth=[], tags=["Protected Assets"], responses={200: OpenApiTypes.BINARY})
    def get(self, request, token):
        with transaction.atomic():
            access = get_object_or_404(
                SupportAttachmentAccessTicket.objects.select_for_update().select_related("message__ticket"), token=token
            )
            if not access.is_valid:
                raise Http404
            access.use_count += 1
            access.save(update_fields=["use_count", "updated_at"])
            attachment = access.message.attachment
            if not attachment:
                raise Http404
            try:
                response = FileResponse(attachment.open("rb"), as_attachment=False, filename=Path(attachment.name).name)
            except (FileNotFoundError, OSError) as exc:
                raise Http404 from exc
        response["Cache-Control"] = "private, no-store, max-age=0"
        response["Pragma"] = "no-cache"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; sandbox"
        return response


class DashboardSupportTicketViewSet(StandardReadOnlyModelViewSet):
    permission_classes = [CanManageSupport]
    serializer_class = DashboardSupportTicketSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["status", "priority", "category", "assigned_to", "user"]
    search_fields = ["subject", "user__full_name", "user__email", "user__phone_number"]
    ordering_fields = ["created_at", "status", "priority", "last_response_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return SupportTicket.objects.filter(is_deleted=False).select_related("user", "assigned_to").prefetch_related("messages__sender")


class DashboardSupportStatusView(APIView):
    permission_classes = [CanManageSupport]
    serializer_class = SupportTicketStatusSerializer

    def patch(self, request, pk: int):
        ticket = get_object_or_404(SupportTicket, pk=pk, is_deleted=False)
        serializer = self.serializer_class(data=request.data, context={"request": request, "ticket": ticket})
        serializer.is_valid(raise_exception=True)
        ticket = serializer.save()
        return success_response(data=_serialize(ticket, request, dashboard=True), message="Support ticket status updated", request=request)


class DashboardSupportPriorityView(APIView):
    permission_classes = [CanManageSupport]
    serializer_class = SupportTicketPrioritySerializer

    def patch(self, request, pk: int):
        ticket = get_object_or_404(SupportTicket, pk=pk, is_deleted=False)
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        old = ticket.priority
        ticket.priority = serializer.validated_data["priority"]
        ticket.save(update_fields=["priority", "updated_at"])
        AuditLogService.log(
            actor=request.user, action=AuditAction.SUPPORT_TICKET_PRIORITY_CHANGED, target=ticket,
            old_value={"priority": old}, new_value={"priority": ticket.priority}, request=request,
        )
        return success_response(data=_serialize(ticket, request, dashboard=True), message="Support ticket priority updated", request=request)


class DashboardSupportAssignView(APIView):
    permission_classes = [CanManageSupport]
    serializer_class = SupportTicketAssignSerializer

    def post(self, request, pk: int):
        ticket = get_object_or_404(SupportTicket, pk=pk, is_deleted=False)
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        old = ticket.assigned_to_id
        ticket.assigned_to = serializer.validated_data["assigned_to"]
        ticket.save(update_fields=["assigned_to", "updated_at"])
        AuditLogService.log(
            actor=request.user, action=AuditAction.SUPPORT_TICKET_ASSIGNED, target=ticket,
            old_value={"assigned_to": old}, new_value={"assigned_to": ticket.assigned_to_id}, request=request,
        )
        return success_response(data=_serialize(ticket, request, dashboard=True), message="Support ticket assigned", request=request)


class DashboardSupportMessageView(APIView):
    permission_classes = [CanManageSupport]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    serializer_class = SupportTicketAddMessageSerializer

    def post(self, request, pk: int):
        ticket = get_object_or_404(SupportTicket, pk=pk, is_deleted=False)
        serializer = self.serializer_class(data=request.data, context={"request": request, "ticket": ticket})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return success_response(
            data=_serialize(ticket, request, dashboard=True), message="Support ticket message added",
            status_code=status.HTTP_201_CREATED, request=request,
        )
