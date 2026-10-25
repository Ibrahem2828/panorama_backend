from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    ExchangeRateViewSet,
    ExpenseExportView,
    ExpenseViewSet,
    FinanceSummaryView,
    InventoryItemViewSet,
    InventoryStockViewSet,
    OrderPaymentViewSet,
    PaymentVoidView,
    PrintJobViewSet,
    PurchaseReminderViewSet,
    PurchaseReportView,
    StockMovementViewSet,
    StockReportView,
)

router = DefaultRouter()
router.register("dashboard/finance/exchange-rates", ExchangeRateViewSet, basename="dashboard-finance-exchange-rates")
router.register("dashboard/finance/purchase-reminders", PurchaseReminderViewSet, basename="dashboard-finance-reminders")
router.register("dashboard/finance/expenses", ExpenseViewSet, basename="dashboard-finance-expenses")
router.register("dashboard/finance/inventory-items", InventoryItemViewSet, basename="dashboard-finance-inventory-items")
# Cashier level (printing.manage: print staff and admins)
router.register("dashboard/printing/stock", InventoryStockViewSet, basename="dashboard-printing-stock")
router.register(
    "dashboard/printing/stock-movements", StockMovementViewSet, basename="dashboard-printing-stock-movements"
)
router.register("dashboard/printing/payments", OrderPaymentViewSet, basename="dashboard-printing-payments")
router.register("dashboard/printing/jobs", PrintJobViewSet, basename="dashboard-printing-jobs")

urlpatterns = [
    path("dashboard/finance/expenses/export/", ExpenseExportView.as_view(), name="dashboard-finance-expenses-export"),
    path(
        "dashboard/finance/purchases-report/", PurchaseReportView.as_view(), name="dashboard-finance-purchases-report"
    ),
    path("dashboard/finance/payments/<int:pk>/void/", PaymentVoidView.as_view(), name="dashboard-finance-payment-void"),
    path("dashboard/finance/summary/", FinanceSummaryView.as_view(), name="dashboard-finance-summary"),
    path("dashboard/finance/stock-report/", StockReportView.as_view(), name="dashboard-finance-stock-report"),
    *router.urls,
]
