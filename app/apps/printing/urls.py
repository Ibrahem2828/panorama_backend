from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    DashboardPrintAssignView,
    DashboardPrintNoteView,
    DashboardPrintOrderViewSet,
    DashboardPrintStatusView,
    MyPrintOrderViewSet,
    PrintOrderCancelView,
    PrintOrderCreateView,
)

router = DefaultRouter()
router.register("printing/orders", MyPrintOrderViewSet, basename="printing-orders")
router.register("dashboard/printing/orders", DashboardPrintOrderViewSet, basename="dashboard-printing-orders")

urlpatterns = [
    path("printing/orders/", PrintOrderCreateView.as_view(), name="printing-order-create"),
    path("printing/orders/my/", MyPrintOrderViewSet.as_view({"get": "list"}), name="printing-orders-my"),
    path("printing/orders/<int:pk>/cancel/", PrintOrderCancelView.as_view(), name="printing-order-cancel"),
    *router.urls,
    path("dashboard/printing/orders/<int:pk>/assign/", DashboardPrintAssignView.as_view(), name="dashboard-printing-assign"),
    path("dashboard/printing/orders/<int:pk>/status/", DashboardPrintStatusView.as_view(), name="dashboard-printing-status"),
    path("dashboard/printing/orders/<int:pk>/note/", DashboardPrintNoteView.as_view(), name="dashboard-printing-note"),
]
