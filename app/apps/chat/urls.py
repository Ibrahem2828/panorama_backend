from django.urls import path

from .views import (
    GroupMessageDeleteView,
    GroupMessageReportView,
    GroupMessageViewSet,
    MessageAttachmentAccessTicketView,
    ProtectedMessageAttachmentStreamView,
)

message_list = GroupMessageViewSet.as_view({"get": "list", "post": "create"})
message_detail = GroupMessageViewSet.as_view({"get": "retrieve", "delete": "destroy"})

urlpatterns = [
    path("groups/<int:group_id>/messages/", message_list, name="group-message-list"),
    path("groups/<int:group_id>/messages/<int:pk>/", message_detail, name="group-message-detail"),
    path("groups/<int:group_id>/messages/<int:message_id>/delete/", GroupMessageDeleteView.as_view(), name="group-message-delete"),
    path("groups/<int:group_id>/messages/<int:message_id>/report/", GroupMessageReportView.as_view(), name="group-message-report"),
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
