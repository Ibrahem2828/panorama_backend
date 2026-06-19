import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.contrib.auth.models import AnonymousUser

from apps.groups.models import Group

from .serializers import MessageSerializer
from .services import ChatMessageService, ChatPermissionService


class GroupChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.group_id = int(self.scope["url_route"]["kwargs"]["group_id"])
        self.room_group_name = f"group_chat_{self.group_id}"
        self.user_group_name = None
        self.user = self.scope.get("user", AnonymousUser())
        ws_token = self.scope.get("ws_token")
        if not ws_token or ws_token.get("group_id") != self.group_id:
            await self.close(code=4401)
            return
        allowed = await self.can_access()
        if not allowed:
            await self.close(code=4403)
            return
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        self.user_group_name = f"group_chat_{self.group_id}_user_{self.user.id}"
        await self.channel_layer.group_add(self.user_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)
        if self.user_group_name:
            await self.channel_layer.group_discard(self.user_group_name, self.channel_name)

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
            if not await self.can_access():
                await self.send(text_data=json.dumps({"type": "error", "error": "You are not allowed to access this group chat."}))
                return
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "typing_event", "user_id": self.user.id, "is_typing": payload.get("is_typing", False)},
            )
            return
        if event_type == "message":
            if not await self.can_send():
                await self.send(text_data=json.dumps({"type": "error", "error": "You are not allowed to send messages in this group."}))
                return
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

    async def force_disconnect(self, event):
        await self.send(text_data=json.dumps({"type": "force_disconnect", "reason": event.get("reason", "permission_changed")}))
        await self.close(code=4403)

    async def permission_changed(self, event):
        await self.send(text_data=json.dumps({"type": "permission_changed", "data": event.get("data", {})}))

    @database_sync_to_async
    def can_access(self):
        try:
            group = Group.objects.get(pk=self.group_id, is_deleted=False)
            return ChatPermissionService.can_access_group_chat(self.user, group)
        except Group.DoesNotExist:
            return False

    @database_sync_to_async
    def can_send(self):
        try:
            group = Group.objects.get(pk=self.group_id, is_deleted=False)
            if not getattr(self.user, "is_active", False):
                return False
            return ChatPermissionService.can_send_message(self.user, group)
        except Group.DoesNotExist:
            return False

    @database_sync_to_async
    def create_message(self, content: str):
        group = Group.objects.get(pk=self.group_id)
        message = ChatMessageService.create_message(group=group, sender=self.user, content=content)
        return MessageSerializer(message).data
