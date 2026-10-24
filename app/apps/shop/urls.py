from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import SaleCreateView, SaleDetailView, SalesReportView, SaleViewSet, SellableItemViewSet

router = DefaultRouter()
router.register("dashboard/shop/items", SellableItemViewSet, basename="dashboard-shop-items")

sale_list = SaleViewSet.as_view({"get": "list"})

urlpatterns = [
    path("dashboard/shop/sales/", sale_list, name="dashboard-shop-sales"),
    path("dashboard/shop/sales/create/", SaleCreateView.as_view(), name="dashboard-shop-sale-create"),
    path("dashboard/shop/sales/report/", SalesReportView.as_view(), name="dashboard-shop-sales-report"),
    path("dashboard/shop/sales/<int:pk>/", SaleDetailView.as_view(), name="dashboard-shop-sale"),
    *router.urls,
]
