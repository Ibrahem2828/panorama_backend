from django.urls import path

from .consumers import GroupChatConsumer
from .middleware import ChatTicketAuthMiddleware

websocket_urlpatterns = [
    path("ws/v1/groups/<int:group_id>/chat/", ChatTicketAuthMiddleware(GroupChatConsumer.as_asgi())),
]
