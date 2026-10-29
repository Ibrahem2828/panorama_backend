from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    DashboardBindingPriceViewSet,
    DashboardCounterOrderCreateView,
    DashboardCounterQuoteView,
    DashboardCoverOptionViewSet,
    DashboardPickupLocationViewSet,
    DashboardPrintAssignView,
    DashboardPrinterViewSet,
    DashboardPrintJobCancelView,
    DashboardPrintJobFileView,
    DashboardPrintJobRetryView,
    DashboardPrintJobStatusView,
    DashboardPrintJobViewSet,
    DashboardPrintNoteView,
    DashboardPrintOrderJobsView,
    DashboardPrintOrderViewSet,
    DashboardPrintPricingRuleViewSet,
    DashboardPrintStatusView,
    DashboardReadyProductViewSet,
    DashboardWireSpecViewSet,
    MyPrintOrderViewSet,
    PrintAgentHeartbeatView,
    PrintConvertDetailView,
    PrintConvertView,
    PrintItemAccessTicketView,
    PrintOptionsView,
    PrintOrderCancelView,
    PrintOrderCreateView,
    PrintPickupLocationViewSet,
    PrintProductViewSet,
    PrintQuoteView,
    ProtectedPrintItemStreamView,
)

router = DefaultRouter()
router.register("printing/orders/my", MyPrintOrderViewSet, basename="my-print-orders")
router.register("printing/pickup-locations", PrintPickupLocationViewSet, basename="print-pickup-locations")
router.register("printing/products", PrintProductViewSet, basename="print-products")
router.register("dashboard/printing/covers", DashboardCoverOptionViewSet, basename="dashboard-print-covers")
router.register("dashboard/printing/wire-specs", DashboardWireSpecViewSet, basename="dashboard-print-wire-specs")
router.register("dashboard/printing/products", DashboardReadyProductViewSet, basename="dashboard-print-products")
router.register("dashboard/printing/printers", DashboardPrinterViewSet, basename="dashboard-printers")
router.register("dashboard/printing/print-jobs", DashboardPrintJobViewSet, basename="dashboard-print-jobs")
router.register("dashboard/printing/orders", DashboardPrintOrderViewSet, basename="dashboard-print-orders")
router.register(
    "dashboard/printing/pricing-rules", DashboardPrintPricingRuleViewSet, basename="dashboard-print-pricing-rules"
)
router.register("dashboard/printing/binding-prices", DashboardBindingPriceViewSet, basename="dashboard-binding-prices")
router.register(
    "dashboard/printing/pickup-locations", DashboardPickupLocationViewSet, basename="dashboard-pickup-locations"
)

urlpatterns = [
    path("printing/options/", PrintOptionsView.as_view(), name="print-options"),
    path("printing/quote/", PrintQuoteView.as_view(), name="print-quote"),
    path(
        "dashboard/printing/orders/<int:pk>/print-jobs/",
        DashboardPrintOrderJobsView.as_view(),
        name="dashboard-print-order-jobs",
    ),
    path(
        "dashboard/printing/print-jobs/<int:pk>/status/",
        DashboardPrintJobStatusView.as_view(),
        name="dashboard-print-job-status",
    ),
    path(
        "dashboard/printing/print-jobs/<int:pk>/retry/",
        DashboardPrintJobRetryView.as_view(),
        name="dashboard-print-job-retry",
    ),
    path(
        "dashboard/printing/print-jobs/<int:pk>/cancel/",
        DashboardPrintJobCancelView.as_view(),
        name="dashboard-print-job-cancel",
    ),
    path("printing/agent/heartbeat/", PrintAgentHeartbeatView.as_view(), name="print-agent-heartbeat"),
    path(
        "dashboard/printing/print-jobs/<int:pk>/file/",
        DashboardPrintJobFileView.as_view(),
        name="dashboard-print-job-file",
    ),
    path("printing/convert/", PrintConvertView.as_view(), name="print-convert"),
    path("printing/convert/<uuid:token>/", PrintConvertDetailView.as_view(), name="print-convert-detail"),
    path("printing/orders/", PrintOrderCreateView.as_view(), name="print-order-create"),
    *router.urls,
    path("printing/orders/<int:pk>/cancel/", PrintOrderCancelView.as_view(), name="print-order-cancel"),
    path(
        "printing/items/<int:pk>/access-ticket/", PrintItemAccessTicketView.as_view(), name="print-item-access-ticket"
    ),
    path(
        "protected-print-items/<uuid:token>/",
        ProtectedPrintItemStreamView.as_view(),
        name="protected-print-item-stream",
    ),
    path(
        "dashboard/printing/orders/<int:pk>/assign/", DashboardPrintAssignView.as_view(), name="dashboard-print-assign"
    ),
    path(
        "dashboard/printing/orders/<int:pk>/status/", DashboardPrintStatusView.as_view(), name="dashboard-print-status"
    ),
    path("dashboard/printing/orders/<int:pk>/note/", DashboardPrintNoteView.as_view(), name="dashboard-print-note"),
    path(
        "dashboard/printing/counter-orders/quote/",
        DashboardCounterQuoteView.as_view(),
        name="dashboard-counter-quote",
    ),
    path(
        "dashboard/printing/counter-orders/",
        DashboardCounterOrderCreateView.as_view(),
        name="dashboard-counter-order-create",
    ),
]
