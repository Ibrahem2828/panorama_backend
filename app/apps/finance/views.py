from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import filters, status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.views import APIView

from apps.accounts.permissions import CanManageFinance, CanManagePrinting
from apps.audit.mixins import AuditedModelWriteMixin
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.pagination import StandardPageNumberPagination
from apps.common.responses import success_response
from apps.common.viewsets import StandardExplicitActionViewSet, StandardModelViewSet
from apps.printing.models import PrintOrderItem

from .models import (
    ExchangeRate,
    Expense,
    ExpenseCategory,
    FinanceCurrency,
    InventoryItem,
    OrderPayment,
    StockMovement,
    StockMovementType,
)
from .serializers import (
    ExchangeRateSerializer,
    ExpenseSerializer,
    InventoryItemSerializer,
    OrderPaymentSerializer,
    PaymentVoidSerializer,
    PrintJobSerializer,
    StockMovementCreateSerializer,
    StockMovementSerializer,
    print_job_row,
)
from .services import FinanceReportService, InventoryService, Period, finished_orders, money


def _int(value: str, name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError({name: "Must be a number."}) from exc


class _FinanceWriteViewSet(AuditedModelWriteMixin, StandardModelViewSet):
    permission_classes = [CanManageFinance]

    def audit_save_kwargs(self, *, creating: bool):
        model = self.get_queryset().model
        names = {f.name for f in model._meta.fields}
        return {"created_by": self.request.user} if creating and "created_by" in names else {}


class ExchangeRateViewSet(_FinanceWriteViewSet):
    serializer_class = ExchangeRateSerializer
    audit_create_action = audit_update_action = audit_delete_action = AuditAction.FINANCE_RATE_CHANGED

    def get_queryset(self):
        return ExchangeRate.objects.filter(is_deleted=False)


class ExpenseViewSet(_FinanceWriteViewSet):
    serializer_class = ExpenseSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["description"]
    ordering_fields = ["spent_on", "amount", "category"]
    ordering = ["-spent_on", "-id"]
    audit_create_action = audit_update_action = audit_delete_action = AuditAction.FINANCE_EXPENSE_CHANGED

    def get_queryset(self):
        queryset = Expense.objects.filter(is_deleted=False)
        params = self.request.query_params
        for field in ("category", "currency"):
            if params.get(field):
                queryset = queryset.filter(**{field: params[field]})
        if params.get("from"):
            queryset = queryset.filter(spent_on__gte=params["from"])
        if params.get("to"):
            queryset = queryset.filter(spent_on__lte=params["to"])
        return queryset


class InventoryItemViewSet(_FinanceWriteViewSet):
    """Items (paper, ink, pens, printers...) are defined and priced by finance managers."""

    serializer_class = InventoryItemSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "sku", "location"]
    ordering_fields = ["name", "category", "quantity", "updated_at"]
    ordering = ["category", "name"]
    audit_create_action = audit_update_action = audit_delete_action = AuditAction.INVENTORY_ITEM_CHANGED

    def get_queryset(self):
        queryset = InventoryItem.objects.filter(is_deleted=False)
        params = self.request.query_params
        if params.get("category"):
            queryset = queryset.filter(category=params["category"])
        if params.get("active") in {"true", "1"}:
            queryset = queryset.filter(is_active=True)
        if params.get("low") in {"true", "1"}:
            queryset = queryset.filter(reorder_level__gt=0, quantity__lte=F("reorder_level"))
        return queryset


class InventoryStockViewSet(StandardExplicitActionViewSet):
    """Cashier view of the stock: see what is on the shelf and record usage, purchases, waste or counts."""

    permission_classes = [CanManagePrinting]
    serializer_class = InventoryItemSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "sku", "location"]
    ordering_fields = ["name", "category", "quantity", "updated_at"]
    ordering = ["category", "name"]

    def get_queryset(self):
        queryset = InventoryItem.objects.filter(is_deleted=False)
        params = self.request.query_params
        if params.get("category"):
            queryset = queryset.filter(category=params["category"])
        if params.get("active") in {"true", "1"}:
            queryset = queryset.filter(is_active=True)
        if params.get("low") in {"true", "1"}:
            queryset = queryset.filter(reorder_level__gt=0, quantity__lte=F("reorder_level"))
        return queryset

    @extend_schema(
        tags=["Dashboard finance"],
        request=StockMovementCreateSerializer,
        responses={201: StockMovementSerializer},
    )
    @action(detail=True, methods=["post"], url_path="movements")
    def record_movement(self, request, pk=None):
        """Record a purchase, usage, waste or count adjustment; the stock quantity changes only through this."""
        body = StockMovementCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        item = self.get_object()
        with transaction.atomic():
            expense = None
            if data["movement_type"] == StockMovementType.PURCHASE and data.get("record_expense"):
                currency = data.get("currency") or item.currency
                expense = Expense.objects.create(
                    spent_on=timezone.localdate(),
                    category=ExpenseCategory.SUPPLIES,
                    description=f"Purchase: {item.name} x {data['quantity']}",
                    amount=money(data["quantity"] * data["unit_cost"]),
                    currency=currency,
                    created_by=request.user,
                )
            movement = InventoryService.record_movement(
                item,
                data["movement_type"],
                data["quantity"],
                actor=request.user,
                unit_cost=data.get("unit_cost"),
                currency=data.get("currency", ""),
                note=data.get("note", ""),
                expense=expense,
            )
            AuditLogService.log(
                actor=request.user,
                action=AuditAction.STOCK_MOVEMENT_RECORDED,
                target=item,
                new_value={
                    "type": movement.movement_type,
                    "quantity": str(movement.quantity),
                    "after": str(movement.quantity_after),
                },
                request=request,
                critical=True,
            )
        return success_response(
            data=StockMovementSerializer(movement).data,
            message="Stock movement recorded",
            status_code=status.HTTP_201_CREATED,
            request=request,
            code="STOCK_MOVEMENT_RECORDED",
        )

    @extend_schema(tags=["Dashboard finance"], responses={200: StockMovementSerializer(many=True)})
    @action(detail=True, methods=["get"], url_path="movement-history")
    def movement_history(self, request, pk=None):
        item = self.get_object()
        rows = item.movements.select_related("item", "created_by")[:200]
        return success_response(data=StockMovementSerializer(rows, many=True).data, request=request)


class StockMovementViewSet(StandardExplicitActionViewSet):
    """Read-only ledger of every stock movement."""

    permission_classes = [CanManagePrinting]
    serializer_class = StockMovementSerializer
    filter_backends = [filters.OrderingFilter]
    ordering = ["-created_at", "-id"]

    def get_queryset(self):
        queryset = StockMovement.objects.filter(is_deleted=False).select_related("item", "created_by")
        params = self.request.query_params
        if params.get("item"):
            queryset = queryset.filter(item__pk=_int(params["item"], "item"))
        if params.get("type"):
            queryset = queryset.filter(movement_type=params["type"])
        return queryset


class OrderPaymentViewSet(StandardExplicitActionViewSet):
    """Collected money. Payments are never edited: a mistaken one is voided (soft-deleted) with a reason."""

    permission_classes = [CanManagePrinting]
    serializer_class = OrderPaymentSerializer
    filter_backends = [filters.OrderingFilter]
    ordering = ["-received_at", "-id"]

    def get_queryset(self):
        queryset = OrderPayment.objects.filter(is_deleted=False).select_related("received_by")
        if self.request.query_params.get("order"):
            queryset = queryset.filter(order__pk=_int(self.request.query_params["order"], "order"))
        return queryset

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            payment = serializer.save()
            AuditLogService.log(
                actor=request.user,
                action=AuditAction.PAYMENT_RECORDED,
                target=payment,
                new_value={"order": payment.order_id, "amount": str(payment.amount), "currency": payment.currency},
                request=request,
                critical=True,
            )
        return success_response(
            data=OrderPaymentSerializer(payment).data,
            message="Payment recorded",
            status_code=status.HTTP_201_CREATED,
            request=request,
            code="PAYMENT_RECORDED",
        )


class PaymentVoidView(APIView):
    """Cancel a mistaken payment (soft delete) with a written reason; finance managers only."""

    permission_classes = [CanManageFinance]

    @extend_schema(tags=["Dashboard finance"], request=PaymentVoidSerializer, responses={200: OrderPaymentSerializer})
    def post(self, request, pk: int):
        body = PaymentVoidSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        with transaction.atomic():
            payment = get_object_or_404(OrderPayment.objects.select_for_update(), pk=pk, is_deleted=False)
            payment.is_deleted = True
            payment.deleted_at = timezone.now()
            payment.save(update_fields=["is_deleted", "deleted_at", "updated_at"])
            AuditLogService.log(
                actor=request.user,
                action=AuditAction.PAYMENT_VOIDED,
                target=payment,
                new_value={"reason": body.validated_data["reason"], "amount": str(payment.amount)},
                request=request,
                critical=True,
            )
        return success_response(
            data=OrderPaymentSerializer(payment).data, message="Payment voided", request=request, code="PAYMENT_VOIDED"
        )


class PrintJobViewSet(StandardExplicitActionViewSet):
    """Finished (delivered) print orders with what each cost, what was collected and the period totals."""

    permission_classes = [CanManagePrinting]
    serializer_class = PrintJobSerializer
    pagination_class = StandardPageNumberPagination

    def get_queryset(self):
        params = self.request.query_params
        period = Period.parse(params.get("from"), params.get("to"))
        queryset = finished_orders(period).select_related("user")
        if params.get("currency"):
            queryset = queryset.filter(currency=params["currency"])
        if params.get("search"):
            term = params["search"]
            queryset = queryset.filter(Q(user__full_name__icontains=term) | Q(user__email__icontains=term))
        return queryset.order_by("-completed_at", "-id")

    @extend_schema(
        tags=["Dashboard finance"],
        parameters=[
            OpenApiParameter("from", OpenApiTypes.DATE),
            OpenApiParameter("to", OpenApiTypes.DATE),
            OpenApiParameter("currency", OpenApiTypes.STR),
            OpenApiParameter("search", OpenApiTypes.STR),
        ],
        responses={200: PrintJobSerializer(many=True)},
    )
    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        totals_by_currency = {
            row["currency"]: {"total": money(row["total"]), "orders": row["orders"]}
            for row in queryset.order_by().values("currency").annotate(total=Sum("total_price"), orders=Count("id"))
        }
        page = self.paginate_queryset(queryset)
        orders = list(page if page is not None else queryset)
        ids = [o.id for o in orders]
        item_totals = {
            row["order_id"]: row
            for row in PrintOrderItem.objects.filter(order_id__in=ids)
            .values("order_id")
            .annotate(files=Count("id"), pages=Sum(F("pages_count") * F("copies")), sheets=Sum("sheets_count"))
        }
        paid = {
            row["order_id"]: row["total"]
            for row in OrderPayment.objects.filter(is_deleted=False, order_id__in=ids)
            .values("order_id")
            .annotate(total=Sum("amount"))
        }
        rows = [
            print_job_row(
                order,
                files=item_totals.get(order.id, {}).get("files", 0),
                pages=item_totals.get(order.id, {}).get("pages", 0) or 0,
                sheets=item_totals.get(order.id, {}).get("sheets", 0) or 0,
                paid=paid.get(order.id, Decimal("0")),
            )
            for order in orders
        ]
        if page is not None:
            response = self.get_paginated_response(rows)
            response.data["data"]["totals_by_currency"] = totals_by_currency
            return response
        return success_response(data={"results": rows, "totals_by_currency": totals_by_currency}, request=request)


class FinanceSummaryView(APIView):
    permission_classes = [CanManageFinance]

    @extend_schema(
        tags=["Dashboard finance"],
        parameters=[
            OpenApiParameter("from", OpenApiTypes.DATE),
            OpenApiParameter("to", OpenApiTypes.DATE),
            OpenApiParameter("currency", OpenApiTypes.STR, description="Reporting currency: SYP (default) or USD"),
        ],
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request):
        currency = request.query_params.get("currency", FinanceCurrency.SYP)
        if currency not in FinanceCurrency.values:
            raise ValidationError({"currency": "Use SYP or USD."})
        period = Period.parse(request.query_params.get("from"), request.query_params.get("to"))
        return success_response(
            data=FinanceReportService.summary(period, currency), request=request, code="FINANCE_SUMMARY"
        )
