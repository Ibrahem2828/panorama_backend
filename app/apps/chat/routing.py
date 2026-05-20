from django.urls import path

from .consumers import GroupChatConsumer

websocket_urlpatterns = [
    path("ws/v1/groups/<int:group_id>/chat/", GroupChatConsumer.as_asgi()),
]
