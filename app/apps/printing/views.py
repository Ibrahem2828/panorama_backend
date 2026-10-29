from __future__ import annotations

import mimetypes
import time
from datetime import timedelta
from pathlib import Path
from typing import cast

from django.conf import settings
from django.db import transaction
from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import content_disposition_header
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework import filters, permissions, serializers, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.accounts.permissions import CanManagePrinting, Capability, PermissionService
from apps.audit.mixins import AuditedModelWriteMixin
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.idempotency import run_idempotent
from apps.common.responses import success_response
from apps.common.throttles import FileTicketRateThrottle, PrintAgentRateThrottle, PrintConvertRateThrottle
from apps.common.viewsets import StandardExplicitActionViewSet, StandardModelViewSet, StandardReadOnlyModelViewSet
from apps.lectures.document_pipeline import DocumentPipelineError, inspect_lecture_document

from .jobs import PrintJobService, render_item
from .models import (
    PrintBindingPrice,
    PrintConversion,
    PrintConversionStatus,
    PrintCoverOption,
    Printer,
    PrintItemAccessTicket,
    PrintJob,
    PrintOrder,
    PrintOrderItem,
    PrintOrderStatus,
    PrintPickupLocation,
    PrintPricingRule,
    ReadyProduct,
    SpiralWireSpec,
)
from .options import PAGES_PER_SHEET_CHOICES, DuplexFlip, NUpDirection, PrintOrientation
from .printers import PrinterService, with_job_counts
from .serializers import (
    CounterOrderCreateSerializer,
    CounterQuoteSerializer,
    DashboardPrintOrderSerializer,
    MobilePrintOrderSerializer,
    PrintBindingPriceSerializer,
    PrintConvertUploadSerializer,
    PrintCoverOptionSerializer,
    PrinterHeartbeatSerializer,
    PrinterSerializer,
    PrintJobCancelSerializer,
    PrintJobStatusSerializer,
    PrintOrderAssignSerializer,
    PrintOrderCreateSerializer,
    PrintOrderNoteSerializer,
    PrintPickupLocationSerializer,
    PrintPricingRuleSerializer,
    PrintQueueJobSerializer,
    PrintQuoteSerializer,
    PrintStatusUpdateSerializer,
    PublicReadyProductSerializer,
    ReadyProductSerializer,
    SpiralWireSpecSerializer,
)
from .services import CounterOrderService, PrintStatusService, default_currency


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
            "exact_total": quote["exact_total"],
            "rounding_adjustment": quote["rounding_adjustment"],
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


CONVERTIBLE_EXTENSIONS = {".doc", ".docx", ".ppt", ".pptx"}


def _conversion_payload(conversion: PrintConversion) -> dict:
    return {
        "id": str(conversion.token),
        "status": conversion.status,
        "filename": conversion.pdf_name,
        "pages_count": conversion.pages_count,
        "expires_at": conversion.expires_at,
        "failure_message": conversion.failure_message,
    }


class PrintConvertView(APIView):
    """Accept a Word/PowerPoint file, convert it to PDF on the conversion worker and return a reference.

    The reference is then sent as ``converted_upload`` on a print item (quote/order). If the worker is slow the
    response is 202 with status ``pending`` and clients poll the detail endpoint.
    """

    parser_classes = [MultiPartParser, FormParser]
    throttle_classes = [PrintConvertRateThrottle]
    serializer_class = PrintConvertUploadSerializer

    @extend_schema(tags=["Printing"], request=PrintConvertUploadSerializer, responses={201: OpenApiTypes.OBJECT})
    def post(self, request):
        from .tasks import convert_print_document

        upload = request.FILES.get("file")
        if not upload:
            raise serializers.ValidationError({"file": "A file is required."})
        if Path(upload.name).suffix.lower() not in CONVERTIBLE_EXTENSIONS:
            raise serializers.ValidationError(
                {"file": "Only Word (doc, docx) and PowerPoint (ppt, pptx) files can be converted."}
            )
        if upload.size > settings.MAX_DOCUMENT_UPLOAD_SIZE:
            raise serializers.ValidationError({"file": "The file is too large."})
        try:
            inspect_lecture_document(upload)
        except DocumentPipelineError as exc:
            raise serializers.ValidationError({"file": str(exc)}) from exc
        upload.seek(0)
        conversion = PrintConversion.objects.create(
            user=request.user,
            original_name=Path(upload.name).name[:255],
            expires_at=timezone.now() + timedelta(seconds=settings.PRINT_CONVERSION_TTL_SECONDS),
        )
        conversion.input_file.save(f"{conversion.token}{Path(upload.name).suffix.lower()}", upload, save=True)
        transaction.on_commit(lambda: convert_print_document.delay(conversion.pk))
        deadline = time.monotonic() + settings.PRINT_CONVERSION_WAIT_SECONDS
        while conversion.status == PrintConversionStatus.PENDING and time.monotonic() < deadline:
            time.sleep(1)
            conversion.refresh_from_db()
        if conversion.status == PrintConversionStatus.FAILED:
            raise serializers.ValidationError({"file": conversion.failure_message or "Document conversion failed."})
        ready = conversion.status == PrintConversionStatus.READY
        return success_response(
            data=_conversion_payload(conversion),
            message="Document converted" if ready else "Document conversion in progress",
            status_code=status.HTTP_201_CREATED if ready else status.HTTP_202_ACCEPTED,
            request=request,
            code="PRINT_CONVERSION_READY" if ready else "PRINT_CONVERSION_PENDING",
        )


class PrintConvertDetailView(APIView):
    @extend_schema(tags=["Printing"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request, token):
        conversion = get_object_or_404(
            PrintConversion, token=token, user=request.user, is_deleted=False, expires_at__gt=timezone.now()
        )
        if conversion.status == PrintConversionStatus.FAILED:
            raise serializers.ValidationError({"file": conversion.failure_message or "Document conversion failed."})
        return success_response(data=_conversion_payload(conversion), request=request, code="PRINT_CONVERSION")


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


class MyPrintOrderViewSet(StandardExplicitActionViewSet):
    serializer_class = MobilePrintOrderSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["status", "priority"]
    ordering_fields = ["created_at", "status"]
    ordering = ["-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return PrintOrder.objects.none()
        return (
            PrintOrder.objects.filter(user=cast(User, self.request.user), is_deleted=False)
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
    filterset_fields = ["status", "priority", "user", "assigned_to", "pickup_location", "source"]
    search_fields = [
        "user__full_name",
        "user__email",
        "user__phone_number",
        "customer_name",
        "customer_phone",
        "id",
    ]
    ordering_fields = ["created_at", "status", "priority", "total_price"]
    ordering = ["-created_at"]

    @extend_schema(
        parameters=[
            OpenApiParameter("created_after", OpenApiTypes.DATE, description="Orders created on or after this date."),
            OpenApiParameter("created_before", OpenApiTypes.DATE, description="Orders created on or before this date."),
        ]
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def _date_param(self, name: str):
        raw = self.request.query_params.get(name)
        if not raw:
            return None
        parsed = parse_date(raw)
        if parsed is None:
            raise serializers.ValidationError({name: "Use an ISO date like 2026-10-29."})
        return parsed

    def get_queryset(self):
        queryset = self._base_queryset()
        created_after = self._date_param("created_after")
        created_before = self._date_param("created_before")
        if created_after:
            queryset = queryset.filter(created_at__date__gte=created_after)
        if created_before:
            queryset = queryset.filter(created_at__date__lte=created_before)
        return queryset

    def _base_queryset(self):
        return (
            PrintOrder.objects.filter(is_deleted=False)
            .select_related("user", "assigned_to", "created_by", "pickup_location")
            .prefetch_related("items__source_file", "status_history__changed_by", "stock_movements__item")
            .annotate(
                paid_total=Coalesce(
                    Sum("payments__amount", filter=Q(payments__is_deleted=False)),
                    Value(0),
                    output_field=DecimalField(max_digits=14, decimal_places=2),
                )
            )
        )


class DashboardCounterQuoteView(APIView):
    """Price the lines of a counter order with the shop's price list, so the cashier sees the rounded price at once."""

    permission_classes = [CanManagePrinting]
    serializer_class = CounterQuoteSerializer

    @extend_schema(tags=["Dashboard"], request=CounterQuoteSerializer)
    def post(self, request):
        serializer = CounterQuoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        quote = CounterOrderService.quote(
            [dict(item) for item in serializer.validated_data["items"]], serializer.validated_data.get("currency")
        )
        for line in quote["items"]:
            line.pop("snapshot", None)
        return success_response(
            data=quote, message="Counter quote calculated", request=request, code="COUNTER_QUOTE_CALCULATED"
        )


class DashboardCounterOrderCreateView(APIView):
    """Record a print order taken at the shop counter: the items, the price charged and what was paid."""

    permission_classes = [CanManagePrinting]
    serializer_class = CounterOrderCreateSerializer

    @extend_schema(
        tags=["Dashboard"], request=CounterOrderCreateSerializer, responses={201: DashboardPrintOrderSerializer}
    )
    def post(self, request):
        serializer = CounterOrderCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        def write():
            order = serializer.save()
            return success_response(
                data=DashboardPrintOrderSerializer(order, context={"request": request}).data,
                message="Counter order created",
                status_code=status.HTTP_201_CREATED,
                request=request,
                code="COUNTER_ORDER_CREATED",
            )

        return run_idempotent(request, "dashboard-counter-order-create", write)


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


class AuditedPrintConfigurationViewSet(AuditedModelWriteMixin, StandardModelViewSet):
    """Audit every pricing/configuration mutation because it affects charged totals."""

    audit_create_action = AuditAction.PRINT_PRICING_CHANGED
    audit_update_action = AuditAction.PRINT_PRICING_CHANGED
    audit_delete_action = AuditAction.PRINT_PRICING_CHANGED


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


# ---- customer: options and ready products --------------------------------------------------------------------------------------


class PrintOptionsView(APIView):
    """Everything the print-options screen needs: the shop's binding types and covers with prices, and the dialog choices."""

    @extend_schema(
        tags=["Printing"],
        parameters=[OpenApiParameter("currency", OpenApiTypes.STR, required=False)],
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request):
        currency = request.query_params.get("currency") or default_currency()
        bindings = PrintBindingPrice.objects.filter(currency=currency, is_active=True, is_deleted=False)
        covers = PrintCoverOption.objects.filter(currency=currency, is_active=True, is_deleted=False)
        data = {
            "currency": currency,
            "pages_per_sheet": list(PAGES_PER_SHEET_CHOICES),
            "orientations": [value for value, _label in PrintOrientation.choices],
            "duplex_flips": [value for value, _label in DuplexFlip.choices],
            "n_up_directions": [value for value, _label in NUpDirection.choices],
            "binding_types": [
                {
                    "code": row.binding,
                    "name": row.display_name,
                    "description": row.description,
                    "kind": row.kind,
                    "price_mode": row.price_mode,
                    "price_per_copy": row.price_per_copy,
                    "price_per_kg": row.price_per_kg,
                }
                for row in bindings
            ],
            "covers": [
                {"code": row.code, "name": row.name, "color_hex": row.color_hex, "price_per_copy": row.price_per_copy}
                for row in covers
            ],
        }
        return success_response(data=data, message="Print options", request=request, code="PRINT_OPTIONS")


class PrintProductViewSet(StandardReadOnlyModelViewSet):
    """Ready-made printed products (notebooks and the like) the customer can order with one tap."""

    serializer_class = PublicReadyProductSerializer
    pagination_class = None

    def get_queryset(self):
        return ReadyProduct.objects.filter(is_active=True, is_deleted=False)


# ---- dashboard: catalog configuration -------------------------------------------------------------------------------------------


class DashboardCoverOptionViewSet(AuditedPrintConfigurationViewSet):
    permission_classes = [CanManagePrinting]
    serializer_class = PrintCoverOptionSerializer
    pagination_class = None

    def get_queryset(self):
        return PrintCoverOption.objects.filter(is_deleted=False)


class DashboardWireSpecViewSet(AuditedPrintConfigurationViewSet):
    permission_classes = [CanManagePrinting]
    serializer_class = SpiralWireSpecSerializer
    pagination_class = None

    def get_queryset(self):
        return SpiralWireSpec.objects.filter(is_deleted=False)


class DashboardReadyProductViewSet(AuditedPrintConfigurationViewSet):
    permission_classes = [CanManagePrinting]
    serializer_class = ReadyProductSerializer
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    pagination_class = None

    def get_queryset(self):
        return ReadyProduct.objects.filter(is_deleted=False)


# ---- dashboard: print jobs ----------------------------------------------------------------------------------------------------------


class DashboardPrintJobViewSet(StandardReadOnlyModelViewSet):
    """The queue of files sent to the printer: ``?status=queued`` is what the silent-print agent polls."""

    permission_classes = [CanManagePrinting]
    serializer_class = PrintQueueJobSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["status", "order", "item"]
    ordering_fields = ["created_at", "status"]
    ordering = ["created_at"]

    def get_queryset(self):
        return PrintJob.objects.filter(is_deleted=False).select_related("item", "created_by", "order")


class DashboardPrintOrderJobsView(APIView):
    """``POST``: queue the order's files (all, or ``item_ids``) for printing and prepare their print-ready PDFs."""

    permission_classes = [CanManagePrinting]

    @extend_schema(tags=["Dashboard"], request=OpenApiTypes.OBJECT, responses={201: PrintQueueJobSerializer(many=True)})
    def post(self, request, pk: int):
        order = get_object_or_404(PrintOrder, pk=pk, is_deleted=False)
        raw_ids = request.data.get("item_ids")
        if raw_ids is not None and not (isinstance(raw_ids, list) and all(isinstance(value, int) for value in raw_ids)):
            raise serializers.ValidationError({"item_ids": "Send a list of file ids."})
        jobs = PrintJobService.create_for_order(
            order,
            request.user,
            item_ids=raw_ids or None,
            printer_name=str(request.data.get("printer_name", "")),
            request=request,
        )
        return success_response(
            data=PrintQueueJobSerializer(jobs, many=True).data,
            message="Print jobs queued",
            status_code=status.HTTP_201_CREATED,
            request=request,
            code="PRINT_JOBS_QUEUED",
        )


class DashboardPrintJobStatusView(APIView):
    permission_classes = [CanManagePrinting]

    @extend_schema(tags=["Dashboard"], request=PrintJobStatusSerializer, responses={200: PrintQueueJobSerializer})
    def patch(self, request, pk: int):
        job = get_object_or_404(PrintJob, pk=pk, is_deleted=False)
        serializer = PrintJobStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        job = PrintJobService.set_status(
            job,
            data["status"],
            request.user,
            printer_name=data.get("printer_name", ""),
            error=data.get("error", ""),
            request=request,
        )
        return success_response(
            data=PrintQueueJobSerializer(job).data,
            message="Print job updated",
            request=request,
            code="PRINT_JOB_UPDATED",
        )


class DashboardPrintJobRetryView(APIView):
    permission_classes = [CanManagePrinting]

    @extend_schema(tags=["Dashboard"], request=None, responses={200: PrintQueueJobSerializer})
    def post(self, request, pk: int):
        job = get_object_or_404(PrintJob, pk=pk, is_deleted=False)
        job = PrintJobService.set_status(job, "queued", request.user, request=request)
        return success_response(
            data=PrintQueueJobSerializer(job).data,
            message="Print job queued again",
            request=request,
            code="PRINT_JOB_REQUEUED",
        )


class DashboardPrintJobFileView(APIView):
    """The print-ready PDF of a job (inline, never cached). Prepared from the stored file each time, so it cannot go stale."""

    permission_classes = [CanManagePrinting]
    throttle_classes = [FileTicketRateThrottle]

    @extend_schema(tags=["Dashboard"], responses={200: OpenApiResponse(description="Print-ready PDF")})
    def get(self, request, pk: int):
        from django.http import HttpResponse

        job = get_object_or_404(PrintJob.objects.select_related("item"), pk=pk, is_deleted=False)
        data, _summary = render_item(job.item)
        AuditLogService.log(
            actor=request.user,
            action=AuditAction.PRINT_FILE_PREPARED,
            target=job,
            new_value={"faces": job.sides_printed},
            request=request,
        )
        response = HttpResponse(data, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="print-job-{job.pk}.pdf"'
        response["Cache-Control"] = "private, no-store, max-age=0"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class DashboardPrintJobCancelView(APIView):
    """``POST``: stop a queued or printing job (optional ``reason``). A finished job answers 409."""

    permission_classes = [CanManagePrinting]
    serializer_class = PrintJobCancelSerializer

    @extend_schema(tags=["Dashboard"], request=PrintJobCancelSerializer, responses={200: PrintQueueJobSerializer})
    def post(self, request, pk: int):
        job = get_object_or_404(PrintJob, pk=pk, is_deleted=False)
        serializer = PrintJobCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job = PrintJobService.cancel(job, request.user, reason=serializer.validated_data["reason"], request=request)
        return success_response(
            data=PrintQueueJobSerializer(job).data,
            message="Print job cancelled",
            request=request,
            code="PRINT_JOB_CANCELLED",
        )


# ---- printers: the registry and the agent heartbeat ---------------------------------------------------------------------------------


class DashboardPrinterViewSet(AuditedModelWriteMixin, StandardModelViewSet):
    """The printers the silent-print agent drives, with whether each is online and its job counts."""

    permission_classes = [CanManagePrinting]
    serializer_class = PrinterSerializer
    pagination_class = None
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["is_active", "is_default"]
    search_fields = ["name", "station", "notes"]
    ordering_fields = ["name", "station", "last_seen_at"]
    ordering = ["name"]
    audit_create_action = audit_update_action = audit_delete_action = AuditAction.PRINTER_CHANGED

    def get_queryset(self):
        return with_job_counts(Printer.objects.filter(is_deleted=False))

    def destroy(self, request, *args, **kwargs):
        printer = self.get_object()
        if not PrinterService.has_jobs(printer):
            return super().destroy(request, *args, **kwargs)
        # It has a history: keep the row (the jobs still point at it) and only take it out of use.
        old = {"is_active": printer.is_active, "is_default": printer.is_default}
        with transaction.atomic():
            printer.is_active = False
            printer.is_default = False
            printer.save(update_fields=["is_active", "is_default", "updated_at"])
            self._audit(
                AuditAction.PRINTER_CHANGED,
                printer,
                old=old,
                new={"is_active": False, "is_default": False},
                changed=["is_active", "is_default"],
            )
        return success_response(
            data=PrinterSerializer(self.get_queryset().get(pk=printer.pk), context={"request": request}).data,
            message="Printer has jobs, so it was deactivated instead of deleted",
            request=request,
            code="PRINTER_DEACTIVATED",
        )


class PrintAgentHeartbeatView(APIView):
    """``POST``: the silent-print agent says it is alive. Authenticated like every other call the agent makes."""

    permission_classes = [CanManagePrinting]
    throttle_classes = [PrintAgentRateThrottle]
    serializer_class = PrinterHeartbeatSerializer

    @extend_schema(tags=["Printing"], request=PrinterHeartbeatSerializer, responses={200: PrinterSerializer})
    def post(self, request):
        serializer = PrinterHeartbeatSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        printer = PrinterService.heartbeat(data["printer_name"], data["status"], data["detail"])
        return success_response(
            data=PrinterSerializer(printer, context={"request": request}).data,
            message="Heartbeat recorded",
            request=request,
            code="PRINTER_HEARTBEAT",
        )
