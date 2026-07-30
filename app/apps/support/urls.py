from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    DashboardSupportAssignView,
    DashboardSupportMessageView,
    DashboardSupportPriorityView,
    DashboardSupportStatusView,
    DashboardSupportTicketViewSet,
    MySupportTicketViewSet,
    SupportAttachmentStreamView,
    SupportAttachmentTicketView,
    SupportTicketCreateView,
    SupportTicketMessageView,
)

router = DefaultRouter()
router.register("support/tickets", MySupportTicketViewSet, basename="support-tickets")
router.register("dashboard/support/tickets", DashboardSupportTicketViewSet, basename="dashboard-support-tickets")

urlpatterns = [
    path("support/tickets/", SupportTicketCreateView.as_view(), name="support-ticket-create"),
    path("support/tickets/my/", MySupportTicketViewSet.as_view({"get": "list"}), name="support-tickets-my"),
    path("support/tickets/<int:pk>/messages/", SupportTicketMessageView.as_view(), name="support-ticket-message"),
    path("support/messages/<int:pk>/attachment-ticket/", SupportAttachmentTicketView.as_view(), name="support-attachment-ticket"),
    path("support/attachments/<uuid:token>/", SupportAttachmentStreamView.as_view(), name="support-attachment-stream"),
    *router.urls,
    path("dashboard/support/tickets/<int:pk>/status/", DashboardSupportStatusView.as_view(), name="dashboard-support-status"),
    path("dashboard/support/tickets/<int:pk>/priority/", DashboardSupportPriorityView.as_view(), name="dashboard-support-priority"),
    path("dashboard/support/tickets/<int:pk>/assign/", DashboardSupportAssignView.as_view(), name="dashboard-support-assign"),
    path("dashboard/support/tickets/<int:pk>/messages/", DashboardSupportMessageView.as_view(), name="dashboard-support-message"),
]
