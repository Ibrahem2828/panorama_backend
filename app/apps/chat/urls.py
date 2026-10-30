from django.urls import path

from .dashboard_views import (
    DashboardChatGroupViewSet,
    DashboardMessageReportResolveView,
    DashboardMessageReportViewSet,
)
from .views import (
    ChatPreferenceListView,
    ChatUnreadView,
    GroupChatHandshakeTicketView,
    GroupChatPreferenceView,
    GroupMessageDeleteView,
    GroupMessageReportView,
    GroupMessageViewSet,
    GroupReceiptView,
    MessageAttachmentAccessTicketView,
    MessageReactionView,
    ProtectedMessageAttachmentStreamView,
)

chat_group_list = DashboardChatGroupViewSet.as_view({"get": "list"})
report_list = DashboardMessageReportViewSet.as_view({"get": "list"})

message_list = GroupMessageViewSet.as_view({"get": "list", "post": "create"})
message_detail = GroupMessageViewSet.as_view({"get": "retrieve", "delete": "destroy"})

urlpatterns = [
    path("dashboard/chat/groups/", chat_group_list, name="dashboard-chat-groups"),
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
    path("groups/<int:group_id>/chat/receipts/", GroupReceiptView.as_view(), name="group-chat-receipts"),
    path("chat/preferences/", ChatPreferenceListView.as_view(), name="chat-preferences"),
    path("groups/<int:group_id>/chat/preferences/", GroupChatPreferenceView.as_view(), name="group-chat-preferences"),
    path(
        "groups/<int:group_id>/messages/<int:message_id>/reaction/",
        MessageReactionView.as_view(),
        name="group-message-reaction",
    ),
    path("chat/unread/", ChatUnreadView.as_view(), name="chat-unread"),
    path(
        "protected-chat-attachments/<uuid:token>/",
        ProtectedMessageAttachmentStreamView.as_view(),
        name="protected-chat-attachment",
    ),
]
