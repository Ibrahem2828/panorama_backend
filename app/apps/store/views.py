"""Student-facing store endpoints (``/api/v1/store/``). Everything needs a login; images are served here, not from /media/."""

from __future__ import annotations

from django.db.models import Exists, F, Min, OuterRef, Prefetch, Q
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import filters, status
from rest_framework.views import APIView

from apps.accounts.permissions import Capability, PermissionService
from apps.common.idempotency import run_idempotent
from apps.common.image_delivery import image_etag, serve_stored_image
from apps.common.responses import success_response
from apps.common.throttles import (
    StoreCartRateThrottle,
    StoreCheckoutRateThrottle,
    StoreImageRateThrottle,
    StoreReviewRateThrottle,
)
from apps.common.viewsets import StandardExplicitActionViewSet, StandardReadOnlyModelViewSet

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
    BrandSerializer,
    CancelSerializer,
    CartAddSerializer,
    CartItemSerializer,
    CartQuantitySerializer,
    CategorySerializer,
    CheckoutSerializer,
    OrderListSerializer,
    OrderSerializer,
    ProductDetailSerializer,
    ProductListSerializer,
    ReviewEditSerializer,
    ReviewSerializer,
    ReviewWriteSerializer,
)

TAGS = ["Store"]


def visible_products():
    """What a student may see: live, active products in an active category (and an active brand, when there is one)."""

    return StoreProduct.objects.filter(
        is_deleted=False,
        is_active=True,
        category__is_active=True,
        category__is_deleted=False,
    ).filter(Q(brand__isnull=True) | Q(brand__is_active=True, brand__is_deleted=False))


def _is_staff_preview(user) -> bool:
    return PermissionService.has(user, Capability.STORE_MANAGE)


# ---- catalogue --------------------------------------------------------------------------------------------------------------------


class BrandListView(APIView):
    @extend_schema(tags=TAGS, responses={200: BrandSerializer(many=True)})
    def get(self, request):
        rows = StoreBrand.objects.filter(is_deleted=False, is_active=True).filter(
            Exists(visible_products().filter(brand=OuterRef("pk")))
        )
        return success_response(data=BrandSerializer(rows, many=True, context={"request": request}).data, request=request)


class CategoryListView(APIView):
    @extend_schema(tags=TAGS, responses={200: CategorySerializer(many=True)})
    def get(self, request):
        rows = StoreCategory.objects.filter(is_deleted=False, is_active=True)
        return success_response(data=CategorySerializer(rows, many=True).data, request=request)


_ORDERINGS = {
    "popular": ["-sold_count", "-rating_count", "-id"],
    "newest": ["-created_at", "-id"],
    "rating": ["-rating_avg", "-rating_count", "-id"],
}


class ProductViewSet(StandardReadOnlyModelViewSet):
    """Product grid and detail. ``ordering``: popular (default), newest, rating, price_asc, price_desc (by ``currency``)."""

    def get_serializer_class(self):
        return ProductDetailSerializer if self.action == "retrieve" else ProductListSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return StoreProduct.objects.none()
        variants = StoreVariant.objects.filter(is_deleted=False, is_active=True).select_related(
            "inventory_item", "product"
        )
        images = StoreProductImage.objects.filter(is_deleted=False)
        shelf = StoreVariant.objects.filter(
            product=OuterRef("pk"),
            is_deleted=False,
            is_active=True,
            inventory_item__is_active=True,
            inventory_item__is_deleted=False,
            inventory_item__quantity__gte=1,
        )
        live = Q(variants__is_active=True, variants__is_deleted=False)
        return (
            visible_products()
            .select_related("brand", "category")
            .prefetch_related(
                Prefetch("variants", queryset=variants),
                Prefetch("images", queryset=images),
            )
            .annotate(
                in_stock_flag=Exists(shelf),
                min_syp=Min(Coalesce("variants__price_syp", "price_syp"), filter=live),
                min_usd=Min(Coalesce("variants__price_usd", "price_usd"), filter=live),
            )
        )

    def filter_queryset(self, queryset):
        params = self.request.query_params
        if self.action != "list":
            return queryset
        if params.get("brand", "").isdigit():
            queryset = queryset.filter(brand_id=int(params["brand"]))
        if params.get("category", "").isdigit():
            queryset = queryset.filter(category_id=int(params["category"]))
        term = params.get("search", "").strip()[:100]
        if term:
            sku = StoreVariant.objects.filter(product=OuterRef("pk"), is_deleted=False, sku__iexact=term)
            queryset = queryset.filter(
                Q(name__icontains=term)
                | Q(brand__name__icontains=term)
                | Q(category__name__icontains=term)
                | Q(description__icontains=term)
                | Exists(sku)
            )
        if params.get("in_stock", "").lower() in {"1", "true", "yes"}:
            queryset = queryset.filter(in_stock_flag=True)
        order = params.get("ordering", "popular")
        currency = "usd" if params.get("currency", "").upper() == "USD" else "syp"
        if order in {"price_asc", "price_desc"}:
            column = f"min_{currency}"
            expression = F(column).asc(nulls_last=True) if order == "price_asc" else F(column).desc(nulls_last=True)
            return queryset.order_by(expression, "-id")
        return queryset.order_by(*_ORDERINGS.get(order, _ORDERINGS["popular"]))

    @extend_schema(
        tags=TAGS,
        parameters=[
            OpenApiParameter("brand", OpenApiTypes.INT),
            OpenApiParameter("category", OpenApiTypes.INT),
            OpenApiParameter("search", OpenApiTypes.STR),
            OpenApiParameter("in_stock", OpenApiTypes.BOOL),
            OpenApiParameter("currency", OpenApiTypes.STR, enum=["SYP", "USD"]),
            OpenApiParameter(
                "ordering", OpenApiTypes.STR, enum=["popular", "newest", "rating", "price_asc", "price_desc"]
            ),
        ],
        responses={200: ProductListSerializer(many=True)},
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(tags=TAGS, responses={200: ProductDetailSerializer})
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)


# ---- reviews ----------------------------------------------------------------------------------------------------------------------


class ProductReviewsView(APIView):
    """``GET`` the visible reviews (paginated); ``POST`` your own review of a product you picked up."""

    def get_throttles(self):
        return [StoreReviewRateThrottle()] if self.request.method == "POST" else super().get_throttles()

    @extend_schema(tags=TAGS, responses={200: ReviewSerializer(many=True)})
    def get(self, request, pk: int):
        product = get_object_or_404(visible_products(), pk=pk)
        rows = StoreReview.objects.filter(product=product, is_deleted=False, is_hidden=False).select_related("user")
        from apps.common.pagination import StandardPageNumberPagination

        paginator = StandardPageNumberPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(ReviewSerializer(page, many=True).data)

    @extend_schema(tags=TAGS, request=ReviewWriteSerializer, responses={201: ReviewSerializer})
    def post(self, request, pk: int):
        product = get_object_or_404(visible_products(), pk=pk)
        serializer = ReviewWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        review = services.post_review(
            request.user, product, serializer.validated_data["rating"], serializer.validated_data["text"]
        )
        return success_response(
            data=ReviewSerializer(review).data,
            message="Review posted",
            status_code=status.HTTP_201_CREATED,
            request=request,
            code="STORE_REVIEW_POSTED",
        )


class MyProductReviewView(APIView):
    """Your own review: ``GET`` it, ``PATCH`` it (allowed once)."""

    def get_throttles(self):
        return [StoreReviewRateThrottle()] if self.request.method == "PATCH" else super().get_throttles()

    @extend_schema(tags=TAGS, responses={200: ReviewSerializer})
    def get(self, request, pk: int):
        review = get_object_or_404(StoreReview, product_id=pk, user=request.user, is_deleted=False)
        return success_response(data=ReviewSerializer(review).data, request=request)

    @extend_schema(tags=TAGS, request=ReviewEditSerializer, responses={200: ReviewSerializer})
    def patch(self, request, pk: int):
        product = get_object_or_404(visible_products(), pk=pk)
        serializer = ReviewEditSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        review = services.edit_review(
            request.user, product, serializer.validated_data.get("rating"), serializer.validated_data.get("text")
        )
        return success_response(data=ReviewSerializer(review).data, message="Review updated", request=request)


# ---- cart -------------------------------------------------------------------------------------------------------------------------


def _cart_payload(cart, request) -> dict:
    items = services.cart_items(cart)
    return {
        "items": CartItemSerializer(items, many=True, context={"request": request}).data,
        "items_count": sum(item.quantity for item in items),
        "max_lines": services.MAX_CART_LINES,
        "totals": services.cart_totals(items),
        "payment": "cash_on_pickup",
    }


class CartView(APIView):
    throttle_classes = [StoreCartRateThrottle]

    @extend_schema(tags=TAGS, responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return success_response(data=_cart_payload(services.get_cart(request.user), request), request=request)

    @extend_schema(tags=TAGS, responses={200: OpenApiTypes.OBJECT})
    def delete(self, request):
        cart = services.cart_clear(request.user)
        return success_response(data=_cart_payload(cart, request), message="Cart cleared", request=request)


class CartItemsView(APIView):
    throttle_classes = [StoreCartRateThrottle]

    @extend_schema(tags=TAGS, request=CartAddSerializer, responses={201: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = CartAddSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        cart = services.cart_add(request.user, serializer.validated_data["variant"], serializer.validated_data["quantity"])
        return success_response(
            data=_cart_payload(cart, request),
            message="Added to cart",
            status_code=status.HTTP_201_CREATED,
            request=request,
            code="STORE_CART_UPDATED",
        )


class CartItemView(APIView):
    throttle_classes = [StoreCartRateThrottle]

    @extend_schema(tags=TAGS, request=CartQuantitySerializer, responses={200: OpenApiTypes.OBJECT})
    def patch(self, request, pk: int):
        serializer = CartQuantitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        cart = services.cart_set_quantity(request.user, pk, serializer.validated_data["quantity"])
        return success_response(data=_cart_payload(cart, request), request=request, code="STORE_CART_UPDATED")

    @extend_schema(tags=TAGS, responses={200: OpenApiTypes.OBJECT})
    def delete(self, request, pk: int):
        cart = services.cart_remove(request.user, pk)
        return success_response(data=_cart_payload(cart, request), request=request, code="STORE_CART_UPDATED")


# ---- checkout and orders ----------------------------------------------------------------------------------------------------------


class CheckoutView(APIView):
    throttle_classes = [StoreCheckoutRateThrottle]

    @extend_schema(tags=TAGS, request=CheckoutSerializer, responses={201: OrderSerializer})
    def post(self, request):
        serializer = CheckoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        def write():
            order = services.checkout(
                request.user,
                serializer.validated_data["currency"],
                serializer.validated_data["notes"],
                request=request,
            )
            order = _order_queryset().get(pk=order.pk)
            return success_response(
                data=OrderSerializer(order, context={"request": request}).data,
                message="Order placed",
                status_code=status.HTTP_201_CREATED,
                request=request,
                code="STORE_ORDER_CREATED",
            )

        return run_idempotent(request, "store-checkout", write)


def _order_queryset():
    return StoreOrder.objects.filter(is_deleted=False).prefetch_related(
        "items__product__images", "history"
    )


class MyOrderViewSet(StandardExplicitActionViewSet):
    """Your orders, newest first. Filter with ``?status=``."""

    filter_backends = [filters.OrderingFilter]
    ordering = ["-created_at", "-id"]

    def get_serializer_class(self):
        return OrderSerializer if self.action == "retrieve" else OrderListSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return StoreOrder.objects.none()
        rows = _order_queryset().filter(user=self.request.user)
        wanted = self.request.query_params.get("status")
        if wanted in StoreOrderStatus.values:
            rows = rows.filter(status=wanted)
        return rows

    @extend_schema(
        tags=TAGS,
        parameters=[OpenApiParameter("status", OpenApiTypes.STR, enum=list(StoreOrderStatus.values))],
        responses={200: OrderListSerializer(many=True)},
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(tags=TAGS, responses={200: OrderSerializer})
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)


class OrderCancelView(APIView):
    throttle_classes = [StoreCartRateThrottle]

    @extend_schema(tags=TAGS, request=CancelSerializer, responses={200: OrderSerializer})
    def post(self, request, pk: int):
        serializer = CancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = get_object_or_404(StoreOrder, pk=pk, user=request.user, is_deleted=False)
        services.change_status(
            order,
            StoreOrderStatus.CANCELLED,
            request.user,
            reason=serializer.validated_data["reason"],
            customer=True,
            request=request,
        )
        order = _order_queryset().get(pk=order.pk)
        return success_response(
            data=OrderSerializer(order, context={"request": request}).data,
            message="Order cancelled",
            request=request,
            code="STORE_ORDER_CANCELLED",
        )


# ---- images -----------------------------------------------------------------------------------------------------------------------


class ProductImageView(APIView):
    """A product picture, behind authentication. ``?variant=thumb`` is the <=480 px WebP preview."""

    throttle_classes = [StoreImageRateThrottle]

    @extend_schema(
        tags=TAGS,
        parameters=[OpenApiParameter("variant", OpenApiTypes.STR, enum=["thumb"])],
        responses={(200, "image/*"): OpenApiTypes.BINARY, 304: None},
    )
    def get(self, request, pk: int):
        rows = StoreProductImage.objects.filter(pk=pk, is_deleted=False, product__is_deleted=False)
        if not _is_staff_preview(request.user):
            rows = rows.filter(product__in=visible_products())
        row = rows.first()
        if row is None:
            raise Http404
        wants_thumb = request.query_params.get("variant") == "thumb"
        field = row.thumb if wants_thumb and row.thumb else row.image
        etag = image_etag(row.pk, field.name, row.updated_at.isoformat())
        return serve_stored_image(request, field, etag)


class BrandLogoView(APIView):
    throttle_classes = [StoreImageRateThrottle]

    @extend_schema(tags=TAGS, responses={(200, "image/*"): OpenApiTypes.BINARY, 304: None})
    def get(self, request, pk: int):
        rows = StoreBrand.objects.filter(pk=pk, is_deleted=False).exclude(logo="")
        if not _is_staff_preview(request.user):
            rows = rows.filter(is_active=True)
        row = rows.first()
        if row is None:
            raise Http404
        return serve_stored_image(request, row.logo, image_etag("brand", row.pk, row.logo.name, row.updated_at.isoformat()))
