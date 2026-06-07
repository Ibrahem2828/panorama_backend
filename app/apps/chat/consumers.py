import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.contrib.auth.models import AnonymousUser

from apps.groups.models import Group

from .serializers import MessageSerializer
from .services import ChatMessageService, ChatPermissionService


class GroupChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.group_id = self.scope["url_route"]["kwargs"]["group_id"]
        self.room_group_name = f"group_chat_{self.group_id}"
        self.user = self.scope.get("user", AnonymousUser())
        allowed = await self.can_access()
        if not allowed:
            await self.close(code=4403)
            return
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        try:
            payload = json.loads(text_data or "{}")
        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({"type": "error", "error": "Invalid message payload."}))
            return
        if not isinstance(payload, dict):
            await self.send(text_data=json.dumps({"type": "error", "error": "Invalid message payload."}))
            return
        event_type = payload.get("type")
        if event_type == "typing":
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "typing_event", "user_id": self.user.id, "is_typing": payload.get("is_typing", False)},
            )
            return
        if event_type == "message":
            try:
                data = await self.create_message(payload.get("content", ""))
            except Exception:
                await self.send(text_data=json.dumps({"type": "error", "error": "Message could not be sent."}))
                return
            await self.channel_layer.group_send(self.room_group_name, {"type": "message_event", "data": data})

    async def message_event(self, event):
        await self.send(text_data=json.dumps({"type": "message", "data": event["data"]}))

    async def typing_event(self, event):
        await self.send(text_data=json.dumps({"type": "typing", "user_id": event["user_id"], "is_typing": event["is_typing"]}))

    @database_sync_to_async
    def can_access(self):
        try:
            group = Group.objects.get(pk=self.group_id)
            return ChatPermissionService.can_access_group_chat(self.user, group)
        except Group.DoesNotExist:
            return False

    @database_sync_to_async
    def create_message(self, content: str):
        group = Group.objects.get(pk=self.group_id)
        message = ChatMessageService.create_message(group=group, sender=self.user, content=content)
        return MessageSerializer(message).data
