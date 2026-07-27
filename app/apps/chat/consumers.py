from __future__ import annotations

import json
import logging
import time

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache

from apps.groups.models import Group

from .serializers import MessageSerializer
from .services import ChatMessageService, ChatPermissionService

logger = logging.getLogger(__name__)

MAX_WEBSOCKET_PAYLOAD_BYTES = 16 * 1024
MAX_MESSAGE_LENGTH = 4000


class GroupChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.group_id = self.scope["url_route"]["kwargs"]["group_id"]
        self.room_group_name = f"group_chat_{self.group_id}"
        self.user = self.scope.get("user", AnonymousUser())
        if not getattr(self.user, "is_authenticated", False):
            await self.close(code=4401)
            return
        if not await self.can_access():
            await self.close(code=4403)
            return
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "room_group_name"):
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        if bytes_data is not None:
            await self.send_error("UNSUPPORTED_PAYLOAD", "Binary WebSocket messages are not supported.")
            return
        raw = text_data or "{}"
        if len(raw.encode("utf-8")) > MAX_WEBSOCKET_PAYLOAD_BYTES:
            await self.send_error("PAYLOAD_TOO_LARGE", "The message payload is too large.")
            return
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            await self.send_error("INVALID_JSON", "Invalid message format.")
            return
        if not isinstance(payload, dict):
            await self.send_error("INVALID_PAYLOAD", "The message payload must be an object.")
            return

        event_type = payload.get("type")
        if event_type == "typing":
            if not await self.consume_rate_limit("typing", 2, 1):
                return
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    "type": "typing_event",
                    "user_id": self.user.id,
                    "is_typing": bool(payload.get("is_typing", False)),
                },
            )
            return
        if event_type != "message":
            await self.send_error("UNKNOWN_EVENT", "Unsupported WebSocket event type.")
            return
        if not await self.consume_rate_limit("message", 20, 60):
            await self.send_error("RATE_LIMITED", "Too many messages. Please slow down.")
            return

        content = str(payload.get("content", "")).strip()
        if not content or len(content) > MAX_MESSAGE_LENGTH:
            await self.send_error("INVALID_CONTENT", "Message content must be between 1 and 4000 characters.")
            return
        try:
            data = await self.create_message(content)
        except Exception:
            logger.exception("WebSocket message creation failed", extra={"user_id": self.user.id, "group_id": self.group_id})
            await self.send_error("MESSAGE_REJECTED", "The message could not be sent.")
            return
        await self.channel_layer.group_send(self.room_group_name, {"type": "message_event", "data": data})

    async def send_error(self, code: str, message: str):
        await self.send(text_data=json.dumps({"type": "error", "code": code, "message": message}))

    async def message_event(self, event):
        await self.send(text_data=json.dumps({"type": "message", "data": event["data"]}))

    async def typing_event(self, event):
        if event["user_id"] == self.user.id:
            return
        await self.send(
            text_data=json.dumps(
                {"type": "typing", "user_id": event["user_id"], "is_typing": event["is_typing"]}
            )
        )

    @database_sync_to_async
    def can_access(self):
        try:
            group = Group.objects.get(pk=self.group_id, is_deleted=False)
            return ChatPermissionService.can_access_group_chat(self.user, group)
        except Group.DoesNotExist:
            return False

    @database_sync_to_async
    def create_message(self, content: str):
        group = Group.objects.get(pk=self.group_id, is_deleted=False)
        message = ChatMessageService.create_message(group=group, sender=self.user, content=content)
        return MessageSerializer(message).data

    @database_sync_to_async
    def consume_rate_limit(self, bucket: str, limit: int, window_seconds: int) -> bool:
        now = int(time.time())
        window = now // window_seconds
        key = f"chat:{bucket}:{self.user.id}:{self.group_id}:{window}"
        try:
            count = cache.incr(key)
        except ValueError:
            cache.set(key, 1, timeout=window_seconds + 2)
            count = 1
        return count <= limit
