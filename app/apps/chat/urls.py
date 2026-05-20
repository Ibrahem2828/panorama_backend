from django.urls import path

from .views import GroupMessageDeleteView, GroupMessageReportView, GroupMessageViewSet

urlpatterns = [
    path("groups/<int:group_id>/messages/", GroupMessageViewSet.as_view({"get": "list", "post": "create"}), name="group-messages"),
    path("groups/<int:group_id>/messages/<int:message_id>/", GroupMessageDeleteView.as_view(), name="group-message-delete"),
    path("groups/<int:group_id>/messages/<int:message_id>/report/", GroupMessageReportView.as_view(), name="group-message-report"),
]
