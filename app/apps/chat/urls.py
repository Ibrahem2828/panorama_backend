from django.urls import path

from .dashboard_views import DashboardMessageReportResolveView, DashboardMessageReportViewSet
from .views import (
    GroupChatHandshakeTicketView,
    GroupMessageDeleteView,
    GroupMessageReportView,
    GroupMessageViewSet,
    MessageAttachmentAccessTicketView,
    ProtectedMessageAttachmentStreamView,
)

report_list = DashboardMessageReportViewSet.as_view({"get": "list"})

message_list = GroupMessageViewSet.as_view({"get": "list", "post": "create"})
message_detail = GroupMessageViewSet.as_view({"get": "retrieve", "delete": "destroy"})

urlpatterns = [
    path("dashboard/chat/reports/", report_list, name="dashboard-chat-reports"),
    path(
        "dashboard/chat/reports/<int:pk>/resolve/",
        DashboardMessageReportResolveView.as_view(),
        name="dashboard-chat-report-resolve",
    ),
    path(
        "groups/<int:group_id>/chat-ticket/",
        GroupChatHandshakeTicketView.as_view(),
        name="group-chat-handshake-ticket",
    ),
    path("groups/<int:group_id>/messages/", message_list, name="group-message-list"),
    path("groups/<int:group_id>/messages/<int:pk>/", message_detail, name="group-message-detail"),
    path(
        "groups/<int:group_id>/messages/<int:message_id>/delete/",
        GroupMessageDeleteView.as_view(),
        name="group-message-delete",
    ),
    path(
        "groups/<int:group_id>/messages/<int:message_id>/report/",
        GroupMessageReportView.as_view(),
        name="group-message-report",
    ),
    path(
        "groups/<int:group_id>/messages/<int:message_id>/attachment-ticket/",
        MessageAttachmentAccessTicketView.as_view(),
        name="group-message-attachment-ticket",
    ),
    path(
        "protected-chat-attachments/<uuid:token>/",
        ProtectedMessageAttachmentStreamView.as_view(),
        name="protected-chat-attachment",
    ),
]
