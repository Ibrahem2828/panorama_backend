from __future__ import annotations

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import filters, status
from rest_framework.views import APIView

from apps.accounts.permissions import CanManagePrinting
from apps.common.idempotency import run_idempotent
from apps.common.responses import success_response
from apps.common.viewsets import StandardExplicitActionViewSet, StandardReadOnlyModelViewSet
from apps.finance.models import InventoryItem
from apps.finance.services import Period

from .models import StationerySale
from .serializers import SaleCreateSerializer, SaleSerializer, SellableItemSerializer
from .services import sales_report


class SellableItemViewSet(StandardReadOnlyModelViewSet):
    """The quick-sale screen's item list: only active items marked as for sale (with their sale price and what is left)."""

    permission_classes = [CanManagePrinting]
    serializer_class = SellableItemSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "sku"]
    ordering = ["name"]

    def get_queryset(self):
        return InventoryItem.objects.filter(is_deleted=False, is_active=True, is_sellable=True)


class SaleViewSet(StandardExplicitActionViewSet):
    """List the recorded sales (``from`` / ``to`` days, default the current month)."""

    permission_classes = [CanManagePrinting]
    serializer_class = SaleSerializer

    def get_queryset(self):
        params = self.request.query_params
        period = Period.parse(params.get("from"), params.get("to"))
        return StationerySale.objects.filter(
            is_deleted=False, sold_at__gte=period.start, sold_at__lt=period.end
        ).prefetch_related("lines")

    @extend_schema(
        tags=["Dashboard"],
        parameters=[OpenApiParameter("from", OpenApiTypes.DATE), OpenApiParameter("to", OpenApiTypes.DATE)],
        responses={200: SaleSerializer(many=True)},
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)


class SaleCreateView(APIView):
    permission_classes = [CanManagePrinting]

    @extend_schema(tags=["Dashboard"], request=SaleCreateSerializer, responses={201: SaleSerializer})
    def post(self, request):
        serializer = SaleCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        def write():
            sale = serializer.save()
            return success_response(
                data=SaleSerializer(sale).data,
                message="Sale recorded",
                status_code=status.HTTP_201_CREATED,
                request=request,
                code="STATIONERY_SALE_RECORDED",
            )

        return run_idempotent(request, "stationery-sale-create", write)


class SaleDetailView(APIView):
    permission_classes = [CanManagePrinting]

    @extend_schema(tags=["Dashboard"], responses={200: SaleSerializer})
    def get(self, request, pk: int):
        sale = get_object_or_404(StationerySale.objects.prefetch_related("lines"), pk=pk, is_deleted=False)
        return success_response(data=SaleSerializer(sale).data, request=request)


class SalesReportView(APIView):
    permission_classes = [CanManagePrinting]

    @extend_schema(
        tags=["Dashboard"],
        parameters=[OpenApiParameter("from", OpenApiTypes.DATE), OpenApiParameter("to", OpenApiTypes.DATE)],
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request):
        period = Period.parse(request.query_params.get("from"), request.query_params.get("to"))
        return success_response(data=sales_report(period), request=request, code="STATIONERY_SALES_REPORT")
