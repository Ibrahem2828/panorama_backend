from __future__ import annotations

import mimetypes
from pathlib import Path

from django.db import transaction
from django.forms.models import model_to_dict
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils.http import content_disposition_header
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework import filters, permissions, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.views import APIView

from apps.accounts.permissions import CanManagePrinting, Capability, PermissionService
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.responses import success_response
from apps.common.throttles import FileTicketRateThrottle
from apps.common.viewsets import StandardModelViewSet, StandardReadOnlyModelViewSet

from .models import (
    PrintBindingPrice,
    PrintItemAccessTicket,
    PrintOrder,
    PrintOrderItem,
    PrintOrderStatus,
    PrintPickupLocation,
    PrintPricingRule,
)
from .serializers import (
    DashboardPrintOrderSerializer,
    MobilePrintOrderSerializer,
    PrintBindingPriceSerializer,
    PrintOrderAssignSerializer,
    PrintOrderCreateSerializer,
    PrintOrderNoteSerializer,
    PrintPickupLocationSerializer,
    PrintPricingRuleSerializer,
    PrintQuoteSerializer,
    PrintStatusUpdateSerializer,
)
from .services import PrintStatusService


class PrintQuoteView(APIView):
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    serializer_class = PrintQuoteSerializer

    @extend_schema(tags=["Printing"], request=PrintQuoteSerializer)
    def post(self, request):
        serializer = PrintQuoteSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        quote = serializer.save()
        data = {
            "total_price": quote["total_price"],
            "currency": quote["currency"],
            "pricing_revision": quote["pricing_revision"],
            "calculated_at": quote["calculated_at"],
            "items": [
                {
                    "pages_count": item.pages_count,
                    "sheets_count": item.sheets_count,
                    "unit_price": item.unit_price,
                    "binding_price": item.binding_price,
                    "subtotal": item.subtotal,
                    "currency": item.currency,
                    "pricing_snapshot": item.snapshot,
                }
                for item in quote["items"]
            ],
        }
        return success_response(
            data=data, message="Print quote calculated by the backend", request=request, code="PRINT_QUOTE_CALCULATED"
        )


class PrintOrderCreateView(APIView):
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    serializer_class = PrintOrderCreateSerializer

    @extend_schema(tags=["Printing"], request=PrintOrderCreateSerializer, responses={201: MobilePrintOrderSerializer})
    def post(self, request):
        serializer = PrintOrderCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        order = serializer.save()
        return success_response(
            data=MobilePrintOrderSerializer(order, context={"request": request}).data,
            message="Print order created successfully",
            status_code=status.HTTP_201_CREATED,
            request=request,
            code="PRINT_ORDER_CREATED",
        )


class PrintPickupLocationViewSet(StandardReadOnlyModelViewSet):
    serializer_class = PrintPickupLocationSerializer
    pagination_class = None

    def get_queryset(self):
        return PrintPickupLocation.objects.filter(is_active=True, is_deleted=False)


class MyPrintOrderViewSet(StandardReadOnlyModelViewSet):
    serializer_class = MobilePrintOrderSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["status", "priority"]
    ordering_fields = ["created_at", "status"]
    ordering = ["-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return PrintOrder.objects.none()
        return (
            PrintOrder.objects.filter(user=self.request.user, is_deleted=False)
            .select_related("pickup_location", "assigned_to")
            .prefetch_related("items__source_file", "status_history__changed_by")
        )


class PrintOrderCancelView(APIView):
    serializer_class = MobilePrintOrderSerializer

    @extend_schema(tags=["Printing"])
    def post(self, request, pk: int):
        order = get_object_or_404(PrintOrder, pk=pk, user=request.user, is_deleted=False)
        order = PrintStatusService.change_status(
            order,
            PrintOrderStatus.CANCELLED,
            request.user,
            public_note="تم إلغاء الطلب من قبل المستخدم.",
            internal_note="Cancelled by user",
            request=request,
        )
        return success_response(
            data=MobilePrintOrderSerializer(order, context={"request": request}).data,
            message="Print order cancelled successfully",
            request=request,
            code="PRINT_ORDER_CANCELLED",
        )


class DashboardPrintOrderViewSet(StandardReadOnlyModelViewSet):
    permission_classes = [CanManagePrinting]
    serializer_class = DashboardPrintOrderSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["status", "priority", "user", "assigned_to", "pickup_location"]
    search_fields = ["user__full_name", "user__email", "user__phone_number", "id"]
    ordering_fields = ["created_at", "status", "priority", "total_price"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return (
            PrintOrder.objects.filter(is_deleted=False)
            .select_related("user", "assigned_to", "pickup_location")
            .prefetch_related("items__source_file", "status_history__changed_by")
        )


class DashboardPrintAssignView(APIView):
    permission_classes = [CanManagePrinting]
    serializer_class = PrintOrderAssignSerializer

    @extend_schema(
        tags=["Dashboard"], request=PrintOrderAssignSerializer, responses={200: DashboardPrintOrderSerializer}
    )
    def patch(self, request, pk: int):
        order = get_object_or_404(PrintOrder, pk=pk, is_deleted=False)
        serializer = PrintOrderAssignSerializer(data=request.data, context={"order": order})
        serializer.is_valid(raise_exception=True)
        old_assignee = order.assigned_to_id
        order = serializer.save()
        AuditLogService.log(
            actor=request.user,
            action=AuditAction.PRINT_ORDER_ASSIGNED,
            target=order,
            old_value={"assigned_to": old_assignee},
            new_value={"assigned_to": order.assigned_to_id},
            request=request,
        )
        return success_response(
            data=DashboardPrintOrderSerializer(order, context={"request": request}).data,
            message="Print order assigned successfully",
            request=request,
        )


class DashboardPrintStatusView(APIView):
    permission_classes = [CanManagePrinting]
    serializer_class = PrintStatusUpdateSerializer

    @extend_schema(
        tags=["Dashboard"], request=PrintStatusUpdateSerializer, responses={200: DashboardPrintOrderSerializer}
    )
    def patch(self, request, pk: int):
        order = get_object_or_404(PrintOrder, pk=pk, is_deleted=False)
        serializer = PrintStatusUpdateSerializer(data=request.data, context={"request": request, "order": order})
        serializer.is_valid(raise_exception=True)
        order = serializer.save()
        return success_response(
            data=DashboardPrintOrderSerializer(order, context={"request": request}).data,
            message="Print order status updated successfully",
            request=request,
        )


class DashboardPrintNoteView(APIView):
    permission_classes = [CanManagePrinting]
    serializer_class = PrintOrderNoteSerializer

    @extend_schema(tags=["Dashboard"], request=PrintOrderNoteSerializer, responses={200: DashboardPrintOrderSerializer})
    def post(self, request, pk: int):
        order = get_object_or_404(PrintOrder, pk=pk, is_deleted=False)
        serializer = PrintOrderNoteSerializer(data=request.data, context={"order": order})
        serializer.is_valid(raise_exception=True)
        order = serializer.save()
        AuditLogService.log(
            actor=request.user,
            action=AuditAction.PRINT_ORDER_NOTE_UPDATED,
            target=order,
            new_value={"internal_note_updated": True},
            request=request,
        )
        return success_response(
            data=DashboardPrintOrderSerializer(order, context={"request": request}).data,
            message="Print order note updated successfully",
            request=request,
        )


class AuditedPrintConfigurationViewSet(StandardModelViewSet):
    """Audit every pricing/configuration mutation because it affects charged totals."""

    def perform_create(self, serializer):
        instance = serializer.save()
        AuditLogService.log(
            actor=self.request.user,
            action=AuditAction.PRINT_PRICING_CHANGED,
            target=instance,
            new_value=model_to_dict(instance),
            request=self.request,
        )

    def perform_update(self, serializer):
        old_value = model_to_dict(serializer.instance)
        instance = serializer.save()
        AuditLogService.log(
            actor=self.request.user,
            action=AuditAction.PRINT_PRICING_CHANGED,
            target=instance,
            old_value=old_value,
            new_value=model_to_dict(instance),
            request=self.request,
        )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        old_value = model_to_dict(instance)
        instance.is_deleted = True
        if hasattr(instance, "is_active"):
            instance.is_active = False
        instance.save()
        AuditLogService.log(
            actor=request.user,
            action=AuditAction.PRINT_PRICING_CHANGED,
            target=instance,
            old_value=old_value,
            new_value={"is_deleted": True, "is_active": getattr(instance, "is_active", None)},
            request=request,
        )
        return success_response(message=self.delete_success_message, request=request)


class DashboardPrintPricingRuleViewSet(AuditedPrintConfigurationViewSet):
    permission_classes = [CanManagePrinting]
    serializer_class = PrintPricingRuleSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["color_mode", "paper_size", "sides", "currency", "is_active"]
    ordering_fields = ["effective_from", "price_per_sheet", "name"]
    ordering = ["-effective_from"]

    def get_queryset(self):
        return PrintPricingRule.objects.filter(is_deleted=False)


class DashboardBindingPriceViewSet(AuditedPrintConfigurationViewSet):
    permission_classes = [CanManagePrinting]
    serializer_class = PrintBindingPriceSerializer
    pagination_class = None

    def get_queryset(self):
        return PrintBindingPrice.objects.filter(is_deleted=False)


class DashboardPickupLocationViewSet(AuditedPrintConfigurationViewSet):
    permission_classes = [CanManagePrinting]
    serializer_class = PrintPickupLocationSerializer
    pagination_class = None

    def get_queryset(self):
        return PrintPickupLocation.objects.filter(is_deleted=False)


class PrintItemAccessTicketView(APIView):
    throttle_classes = [FileTicketRateThrottle]

    @extend_schema(tags=["Printing"], request=None, responses={201: OpenApiTypes.OBJECT})
    def post(self, request, pk: int):
        item = get_object_or_404(
            PrintOrderItem.objects.select_related("order", "source_file"),
            pk=pk,
            is_deleted=False,
            order__is_deleted=False,
        )
        if item.order.user_id != request.user.id and not PermissionService.has(
            request.user, Capability.PRINTING_MANAGE
        ):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("You are not allowed to preview this print item.")
        ticket = PrintItemAccessTicket.issue(item, request.user)
        preview_url = request.build_absolute_uri(f"/api/v1/protected-print-items/{ticket.token}/")
        return success_response(
            data={"preview_url": preview_url, "expires_at": ticket.expires_at, "download_allowed": False},
            message="Protected print item ticket issued",
            status_code=status.HTTP_201_CREATED,
            request=request,
            code="PRINT_ITEM_TICKET_ISSUED",
        )


class ProtectedPrintItemStreamView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        tags=["Protected Assets"], responses={200: OpenApiResponse(description="Inline protected print item")}
    )
    def get(self, request, token):
        with transaction.atomic():
            ticket = (
                PrintItemAccessTicket.objects.select_for_update()
                .select_related("item__source_file", "item__order")
                .filter(token=token, is_deleted=False)
                .first()
            )
            if not ticket or not ticket.is_valid or ticket.requested_by_id != request.user.id:
                raise Http404("The protected print item link is invalid or expired.")
            item = ticket.item
            if item.order.user_id != request.user.id and not PermissionService.has(
                request.user, Capability.PRINTING_MANAGE
            ):
                raise Http404("The protected print item link is invalid or expired.")
            source = item.uploaded_file or (item.source_file.file if item.source_file else None)
            if not source:
                raise Http404("Print item file is unavailable.")
            ticket.use_count += 1
            ticket.save(update_fields=["use_count", "updated_at"])
        filename = Path(source.name).name
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        response = FileResponse(source.open("rb"), content_type=content_type)
        content_disposition = content_disposition_header(False, filename)
        if content_disposition:
            response["Content-Disposition"] = content_disposition
        response["Cache-Control"] = "private, no-store, max-age=0"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = (
            "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"
        )
        return response
