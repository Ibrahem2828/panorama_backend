from django.urls import path

from .views import GroupChatWebSocketTokenView, GroupMessageDeleteView, GroupMessageReportView, GroupMessageViewSet

urlpatterns = [
    path("groups/<int:group_id>/chat/ws-token/", GroupChatWebSocketTokenView.as_view(), name="group-chat-ws-token"),
    path("groups/<int:group_id>/messages/", GroupMessageViewSet.as_view({"get": "list", "post": "create"}), name="group-messages"),
    path("groups/<int:group_id>/messages/<int:message_id>/", GroupMessageDeleteView.as_view(), name="group-message-delete"),
    path("groups/<int:group_id>/messages/<int:message_id>/report/", GroupMessageReportView.as_view(), name="group-message-report"),
]
