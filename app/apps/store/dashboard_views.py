"""Staff endpoints of the store (``/api/v1/dashboard/store/``), all behind the ``store.manage`` capability."""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Count, F, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import filters, status
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.views import APIView

from apps.accounts.permissions import CanManageStore
from apps.audit.mixins import AuditedModelWriteMixin
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.responses import success_response
from apps.common.viewsets import StandardExplicitActionViewSet, StandardModelViewSet
from apps.finance.models import StockMovementType
from apps.finance.services import InventoryService

from . import services
from .models import (
    StoreBrand,
    StoreCategory,
    StoreOrder,
    StoreOrderStatus,
    StoreProduct,
    StoreProductImage,
    StoreReview,
    StoreVariant,
)
from .serializers import (
    DashboardBrandSerializer,
    DashboardCategorySerializer,
    DashboardImageSerializer,
    DashboardOrderListSerializer,
    DashboardOrderSerializer,
    DashboardProductSerializer,
    DashboardReviewSerializer,
    DashboardVariantSerializer,
    HideReviewSerializer,
    OrderStatusSerializer,
    StockAdjustSerializer,
)

TAGS = ["Dashboard store"]
_PRICE_FIELDS = {"price_syp", "price_usd"}


class _CatalogViewSet(AuditedModelWriteMixin, StandardModelViewSet):
    """CRUD with one audit row per write; a write that touches a price is logged as a price change."""

    permission_classes = [CanManageStore]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    audit_create_action = AuditAction.STORE_CATALOG_CHANGED
    audit_update_action = AuditAction.STORE_CATALOG_CHANGED
    audit_delete_action = AuditAction.STORE_CATALOG_CHANGED

    def resolve_update_action(self, old, new):
        return AuditAction.STORE_PRICE_CHANGED if _PRICE_FIELDS & (set(old) | set(new)) else self.audit_update_action

    def refuse_when_in_use(self, queryset) -> None:
        if queryset.filter(is_deleted=False).exists():
            from .services import StoreError

            raise StoreError("IN_USE", "Move or delete its products first.", status.HTTP_409_CONFLICT)


class DashboardBrandViewSet(_CatalogViewSet):
    serializer_class = DashboardBrandSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["is_active"]
    search_fields = ["name"]
    ordering_fields = ["name", "sort_order", "created_at"]

    def get_queryset(self):
        return StoreBrand.objects.filter(is_deleted=False)

    def destroy(self, request, *args, **kwargs):
        self.refuse_when_in_use(self.get_object().products.all())
        return super().destroy(request, *args, **kwargs)


class DashboardCategoryViewSet(_CatalogViewSet):
    serializer_class = DashboardCategorySerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["is_active"]
    ordering_fields = ["name", "sort_order"]
    pagination_class = None

    def get_queryset(self):
        return StoreCategory.objects.filter(is_deleted=False)

    def destroy(self, request, *args, **kwargs):
        self.refuse_when_in_use(self.get_object().products.all())
        return super().destroy(request, *args, **kwargs)


class DashboardProductViewSet(_CatalogViewSet):
    serializer_class = DashboardProductSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["brand", "category", "is_active"]
    search_fields = ["name", "brand__name"]
    ordering_fields = ["name", "sort_order", "created_at", "sold_count", "rating_avg"]

    def get_queryset(self):
        return StoreProduct.objects.filter(is_deleted=False).select_related("brand", "category")


class DashboardVariantViewSet(_CatalogViewSet):
    serializer_class = DashboardVariantSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["product", "is_active"]
    search_fields = ["sku", "colour_name", "size_label", "product__name"]
    ordering_fields = ["sort_order", "created_at"]

    def get_queryset(self):
        return StoreVariant.objects.filter(is_deleted=False, product__is_deleted=False).select_related(
            "product", "inventory_item"
        )

    def destroy(self, request, *args, **kwargs):
        variant = self.get_object()
        response = super().destroy(request, *args, **kwargs)
        services.sync_inventory_item(variant)  # off the counter's quick-sale list too
        return response

    @extend_schema(tags=TAGS, request=StockAdjustSerializer, responses={201: OpenApiTypes.OBJECT})
    def stock(self, request, pk=None):
        """Book a stock movement on the variant's shelf row (the same log the inventory screens use)."""

        variant = self.get_object()
        serializer = StockAdjustSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            item = services.ensure_inventory_item(variant, actor=request.user)
            movement = InventoryService.record_movement(
                item,
                StockMovementType(data["movement_type"]),
                Decimal(data["quantity"]),
                actor=request.user,
                note=data["note"] or f"متجر القرطاسية: {variant}",
            )
            AuditLogService.log(
                actor=request.user,
                action=AuditAction.STOCK_MOVEMENT_RECORDED,
                target=item,
                new_value={
                    "type": movement.movement_type,
                    "quantity": str(movement.quantity),
                    "after": str(movement.quantity_after),
                    "store_variant": variant.pk,
                },
                request=request,
                critical=True,
            )
        return success_response(
            data={"variant": variant.pk, "stock": int(movement.quantity_after)},
            message="Stock movement recorded",
            status_code=status.HTTP_201_CREATED,
            request=request,
            code="STORE_STOCK_RECORDED",
        )


class DashboardImageViewSet(_CatalogViewSet):
    """Product pictures. Send ``multipart/form-data`` with ``image``; the original is kept and a <=480 px WebP is made."""

    serializer_class = DashboardImageSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["product", "variant", "is_primary"]
    ordering_fields = ["sort_order", "created_at"]
    pagination_class = None

    def get_queryset(self):
        return StoreProductImage.objects.filter(is_deleted=False, product__is_deleted=False)

    def _finish(self, image: StoreProductImage, *, rebuild: bool) -> None:
        if rebuild:
            services.attach_thumbnail(image)
        has_primary = StoreProductImage.objects.filter(product_id=image.product_id, is_primary=True, is_deleted=False)
        if image.is_primary or not has_primary.exists():
            services.set_primary_image(image)

    def perform_create(self, serializer):
        with transaction.atomic():
            super().perform_create(serializer)
            self._finish(serializer.instance, rebuild=True)

    def perform_update(self, serializer):
        rebuild = "image" in serializer.validated_data
        with transaction.atomic():
            super().perform_update(serializer)
            self._finish(serializer.instance, rebuild=rebuild)


# ---- orders -----------------------------------------------------------------------------------------------------------------------


class DashboardOrderViewSet(StandardExplicitActionViewSet):
    """Orders, newest first. Filters: ``status``, ``q`` (order number, customer name or phone), ``from``/``to`` (date)."""

    permission_classes = [CanManageStore]
    filter_backends = [filters.OrderingFilter]
    ordering = ["-created_at", "-id"]

    def get_serializer_class(self):
        return DashboardOrderSerializer if self.action == "retrieve" else DashboardOrderListSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return StoreOrder.objects.none()
        rows = StoreOrder.objects.filter(is_deleted=False).select_related("user").prefetch_related("items", "history")
        params = self.request.query_params
        if params.get("status") in StoreOrderStatus.values:
            rows = rows.filter(status=params["status"])
        term = params.get("q", "").strip()[:60]
        if term:
            digits = term.upper().removeprefix("ST-").lstrip("0")
            number = Q(pk=int(digits)) if digits.isdigit() and len(digits) < 12 else Q(pk__in=[])
            rows = rows.filter(number | Q(user__full_name__icontains=term) | Q(user__phone_number__icontains=term))
        if params.get("from"):
            rows = rows.filter(created_at__date__gte=params["from"]) if _is_date(params["from"]) else rows
        if params.get("to"):
            rows = rows.filter(created_at__date__lte=params["to"]) if _is_date(params["to"]) else rows
        return rows

    @extend_schema(
        tags=TAGS,
        parameters=[
            OpenApiParameter("status", OpenApiTypes.STR, enum=list(StoreOrderStatus.values)),
            OpenApiParameter("q", OpenApiTypes.STR),
            OpenApiParameter("from", OpenApiTypes.DATE),
            OpenApiParameter("to", OpenApiTypes.DATE),
        ],
        responses={200: DashboardOrderListSerializer(many=True)},
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(tags=TAGS, responses={200: DashboardOrderSerializer})
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)


def _is_date(value: str) -> bool:
    from django.utils.dateparse import parse_date

    return parse_date(value) is not None


class DashboardOrderStatusView(APIView):
    permission_classes = [CanManageStore]

    @extend_schema(tags=TAGS, request=OrderStatusSerializer, responses={200: DashboardOrderSerializer})
    def patch(self, request, pk: int):
        serializer = OrderStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = get_object_or_404(StoreOrder, pk=pk, is_deleted=False)
        data = serializer.validated_data
        services.change_status(order, data["status"], request.user, reason=data["reason"], note=data["note"], request=request)
        order = StoreOrder.objects.select_related("user").prefetch_related("items", "history").get(pk=pk)
        return success_response(
            data=DashboardOrderSerializer(order).data,
            message="Order updated",
            request=request,
            code="STORE_ORDER_STATUS_CHANGED",
        )


# ---- reviews ----------------------------------------------------------------------------------------------------------------------


class DashboardReviewViewSet(StandardExplicitActionViewSet):
    permission_classes = [CanManageStore]
    serializer_class = DashboardReviewSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["product", "rating", "is_hidden"]
    ordering = ["-created_at", "-id"]

    def get_queryset(self):
        return StoreReview.objects.filter(is_deleted=False).select_related("product", "user")


class _ModerateView(APIView):
    permission_classes = [CanManageStore]
    hide = True

    @extend_schema(tags=TAGS, request=HideReviewSerializer, responses={200: DashboardReviewSerializer})
    def post(self, request, pk: int):
        serializer = HideReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        review = get_object_or_404(StoreReview, pk=pk, is_deleted=False)
        if self.hide and not serializer.validated_data["reason"].strip():
            raise ValidationError({"reason": "A reason is required to hide a review."})
        review = services.moderate_review(
            review, request.user, hide=self.hide, reason=serializer.validated_data["reason"], request=request
        )
        return success_response(data=DashboardReviewSerializer(review).data, request=request, code="STORE_REVIEW_MODERATED")


class HideReviewView(_ModerateView):
    hide = True


class UnhideReviewView(_ModerateView):
    hide = False


# ---- summary ----------------------------------------------------------------------------------------------------------------------


class DashboardSummaryView(APIView):
    """Order counts by status, today's orders and the variants that are out of stock or at their reorder level."""

    permission_classes = [CanManageStore]

    @extend_schema(tags=TAGS, responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        orders = StoreOrder.objects.filter(is_deleted=False)
        counts = {row["status"]: row["n"] for row in orders.order_by().values("status").annotate(n=Count("id"))}
        low = (
            StoreVariant.objects.filter(
                is_deleted=False,
                is_active=True,
                product__is_deleted=False,
                product__is_active=True,
                inventory_item__isnull=False,
            )
            .filter(
                Q(inventory_item__quantity__lte=0)
                | Q(inventory_item__reorder_level__gt=0, inventory_item__quantity__lte=F("inventory_item__reorder_level"))
            )
            .select_related("product", "inventory_item")
            .order_by("inventory_item__quantity", "id")[:20]
        )
        return success_response(
            data={
                "orders_by_status": {value: counts.get(value, 0) for value in StoreOrderStatus.values},
                "orders_today": orders.filter(created_at__date=timezone.localdate()).count(),
                "products_active": StoreProduct.objects.filter(is_deleted=False, is_active=True).count(),
                "reviews_hidden": StoreReview.objects.filter(is_deleted=False, is_hidden=True).count(),
                "low_stock": [
                    {
                        "variant": variant.pk,
                        "product": variant.product.name,
                        "label": variant.label,
                        "available": variant.available,
                        "reorder_level": int(variant.inventory_item.reorder_level),
                    }
                    for variant in low
                ],
            },
            request=request,
            code="STORE_SUMMARY",
        )
