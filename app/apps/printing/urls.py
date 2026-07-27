from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    DashboardBindingPriceViewSet,
    DashboardPickupLocationViewSet,
    DashboardPrintAssignView,
    DashboardPrintNoteView,
    DashboardPrintOrderViewSet,
    DashboardPrintPricingRuleViewSet,
    DashboardPrintStatusView,
    MyPrintOrderViewSet,
    PrintItemAccessTicketView,
    PrintOrderCancelView,
    PrintOrderCreateView,
    PrintPickupLocationViewSet,
    PrintQuoteView,
    ProtectedPrintItemStreamView,
)

router = DefaultRouter()
router.register("printing/orders/my", MyPrintOrderViewSet, basename="my-print-orders")
router.register("printing/pickup-locations", PrintPickupLocationViewSet, basename="print-pickup-locations")
router.register("dashboard/printing/orders", DashboardPrintOrderViewSet, basename="dashboard-print-orders")
router.register("dashboard/printing/pricing-rules", DashboardPrintPricingRuleViewSet, basename="dashboard-print-pricing-rules")
router.register("dashboard/printing/binding-prices", DashboardBindingPriceViewSet, basename="dashboard-binding-prices")
router.register("dashboard/printing/pickup-locations", DashboardPickupLocationViewSet, basename="dashboard-pickup-locations")

urlpatterns = [
    path("printing/quote/", PrintQuoteView.as_view(), name="print-quote"),
    path("printing/orders/", PrintOrderCreateView.as_view(), name="print-order-create"),
    *router.urls,
    path("printing/orders/<int:pk>/cancel/", PrintOrderCancelView.as_view(), name="print-order-cancel"),
    path("printing/items/<int:pk>/access-ticket/", PrintItemAccessTicketView.as_view(), name="print-item-access-ticket"),
    path("protected-print-items/<uuid:token>/", ProtectedPrintItemStreamView.as_view(), name="protected-print-item-stream"),
    path("dashboard/printing/orders/<int:pk>/assign/", DashboardPrintAssignView.as_view(), name="dashboard-print-assign"),
    path("dashboard/printing/orders/<int:pk>/status/", DashboardPrintStatusView.as_view(), name="dashboard-print-status"),
    path("dashboard/printing/orders/<int:pk>/note/", DashboardPrintNoteView.as_view(), name="dashboard-print-note"),
]
