from django.urls import path
from rest_framework.routers import DefaultRouter

from . import dashboard_views as dash
from . import views

router = DefaultRouter()
router.register("store/products", views.ProductViewSet, basename="store-products")
router.register("store/orders", views.MyOrderViewSet, basename="store-orders")
router.register("dashboard/store/brands", dash.DashboardBrandViewSet, basename="dashboard-store-brands")
router.register("dashboard/store/categories", dash.DashboardCategoryViewSet, basename="dashboard-store-categories")
router.register("dashboard/store/products", dash.DashboardProductViewSet, basename="dashboard-store-products")
router.register("dashboard/store/variants", dash.DashboardVariantViewSet, basename="dashboard-store-variants")
router.register("dashboard/store/images", dash.DashboardImageViewSet, basename="dashboard-store-images")
router.register("dashboard/store/orders", dash.DashboardOrderViewSet, basename="dashboard-store-orders")
router.register("dashboard/store/reviews", dash.DashboardReviewViewSet, basename="dashboard-store-reviews")

urlpatterns = [
    path("store/brands/", views.BrandListView.as_view(), name="store-brands"),
    path("store/brands/<int:pk>/logo/", views.BrandLogoView.as_view(), name="store-brand-logo"),
    path("store/categories/", views.CategoryListView.as_view(), name="store-categories"),
    path("store/images/<int:pk>/", views.ProductImageView.as_view(), name="store-image"),
    path("store/products/<int:pk>/reviews/", views.ProductReviewsView.as_view(), name="store-product-reviews"),
    path("store/products/<int:pk>/reviews/mine/", views.MyProductReviewView.as_view(), name="store-my-review"),
    path("store/cart/", views.CartView.as_view(), name="store-cart"),
    path("store/cart/items/", views.CartItemsView.as_view(), name="store-cart-items"),
    path("store/cart/items/<int:pk>/", views.CartItemView.as_view(), name="store-cart-item"),
    path("store/checkout/", views.CheckoutView.as_view(), name="store-checkout"),
    path("store/orders/<int:pk>/cancel/", views.OrderCancelView.as_view(), name="store-order-cancel"),
    path("dashboard/store/summary/", dash.DashboardSummaryView.as_view(), name="dashboard-store-summary"),
    path(
        "dashboard/store/variants/<int:pk>/stock/",
        dash.DashboardVariantViewSet.as_view({"post": "stock"}),
        name="dashboard-store-variant-stock",
    ),
    path("dashboard/store/orders/<int:pk>/status/", dash.DashboardOrderStatusView.as_view(), name="dashboard-store-order-status"),
    path("dashboard/store/reviews/<int:pk>/hide/", dash.HideReviewView.as_view(), name="dashboard-store-review-hide"),
    path("dashboard/store/reviews/<int:pk>/unhide/", dash.UnhideReviewView.as_view(), name="dashboard-store-review-unhide"),
    *router.urls,
]
