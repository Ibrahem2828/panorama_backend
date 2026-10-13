from __future__ import annotations

import json
import logging
import time
from hashlib import sha256

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
AUTHENTICATION_CLOSE_CODE = 4401
AUTHORIZATION_CLOSE_CODE = 4403


#: Authorization (user active, session_version, group active, membership/ban) is cached per connection for this
#: long. Typing frames use the cache only; a message SEND always re-checks (it is a write). So a revoked membership,
#: ban, deactivation or session_version bump closes the socket on the first inbound frame after the cache expires:
#: at most AUTHORIZATION_CACHE_TTL_SECONDS (plus the gap until the client's next frame) of stale typing access.
AUTHORIZATION_CACHE_TTL_SECONDS = 20

#: In-memory per-connection frame limiter (token bucket), applied before any DB or cache access. Frames beyond
#: the budget are dropped silently (an ``RATE_LIMITED`` error is sent at most once per second).
FRAME_BUCKET_CAPACITY = 30
FRAME_BUCKET_REFILL_PER_SECOND = 15.0


def _monotonic() -> float:
    """Clock used for the authorization cache and frame bucket (patched in tests)."""
    return time.monotonic()


class GroupChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.group_id = self.scope["url_route"]["kwargs"]["group_id"]
        self.room_group_name = f"group_chat_{self.group_id}"
        self.user = self.scope.get("user", AnonymousUser())
        self.session_version = self.scope.get("ws_session_version")
        self._auth_valid_until = 0.0
        self._frame_tokens = float(FRAME_BUCKET_CAPACITY)
        self._frame_refilled_at = _monotonic()
        self._last_rate_error_at = 0.0
        if not getattr(self.user, "is_authenticated", False):
            await self.close(code=AUTHENTICATION_CLOSE_CODE)
            return
        close_code = await self.refresh_authorization()
        if close_code is not None:
            await self.close(code=close_code)
            return
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "room_group_name"):
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    def _take_frame_token(self) -> bool:
        now = _monotonic()
        elapsed = max(0.0, now - self._frame_refilled_at)
        self._frame_refilled_at = now
        self._frame_tokens = min(
            float(FRAME_BUCKET_CAPACITY), self._frame_tokens + elapsed * FRAME_BUCKET_REFILL_PER_SECOND
        )
        if self._frame_tokens < 1.0:
            return False
        self._frame_tokens -= 1.0
        return True

    async def refresh_authorization(self) -> int | None:
        """Run the DB authorization check and cache a successful result for the TTL."""
        close_code = await self.access_close_code()
        self._auth_valid_until = _monotonic() + AUTHORIZATION_CACHE_TTL_SECONDS if close_code is None else 0.0
        return close_code

    async def cached_authorization(self) -> int | None:
        """Return the close code for read-only frames, hitting the DB only when the cache expired."""
        if _monotonic() < self._auth_valid_until:
            return None
        return await self.refresh_authorization()

    async def receive(self, text_data=None, bytes_data=None):
        # 1. Per-connection rate limit FIRST: a flood must cost no DB and no cache round trip.
        if not self._take_frame_token():
            now = _monotonic()
            if now - self._last_rate_error_at >= 1.0:
                self._last_rate_error_at = now
                await self.send_error("RATE_LIMITED", "Too many messages. Please slow down.")
            return
        # 2. Frame size/shape validation, still without touching the database.
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
            # Typing is read-only: it relies on the cached authorization (refreshed at most every TTL seconds).
            close_code = await self.cached_authorization()
            if close_code is not None:
                await self.close(code=close_code)
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
        # A send is a write: never trust the cache, always re-check (and re-prime the cache with the result).
        close_code = await self.refresh_authorization()
        if close_code is not None:
            await self.close(code=close_code)
            return
        try:
            data = await self.create_message(content)
        except Exception:
            logger.exception(
                "WebSocket message creation failed",
                extra={
                    "user_id_hash": sha256(str(self.user.id).encode("utf-8")).hexdigest()[:16],
                    "group_id": self.group_id,
                },
            )
            await self.send_error("MESSAGE_REJECTED", "The message could not be sent.")
            return
        await self.channel_layer.group_send(self.room_group_name, {"type": "message_event", "data": data})

    async def send_error(self, code: str, message: str):
        await self.send(text_data=json.dumps({"type": "error", "code": code, "message": message}))

    async def _deliverable(self) -> bool:
        """Gate for frames pushed from the channel layer: cached authorization only (re-read at most every TTL)."""
        close_code = await self.cached_authorization()
        if close_code is not None:
            await self.close(code=close_code)
            return False
        return True

    async def message_event(self, event):
        # Fan-out of a message created by any socket or by the REST API.
        if await self._deliverable():
            await self.send(text_data=json.dumps({"type": "message", "data": event["data"]}))

    async def message_deleted(self, event):
        # ``group_send({"type": "message.deleted"})`` is dispatched to this method (dots become underscores).
        if await self._deliverable():
            await self.send(text_data=json.dumps({"type": "message.deleted", "data": event["data"]}))

    async def typing_event(self, event):
        if event["user_id"] == self.user.id:
            return
        await self.send(
            text_data=json.dumps({"type": "typing", "user_id": event["user_id"], "is_typing": event["is_typing"]})
        )

    @database_sync_to_async
    def access_close_code(self) -> int | None:
        """Return the stable close-code category for the current socket state."""

        try:
            from apps.accounts.models import User

            user = User.objects.get(pk=self.user.pk, is_active=True, is_deleted=False)
        except User.DoesNotExist:
            return AUTHENTICATION_CLOSE_CODE

        if self.session_version is not None and user.session_version != self.session_version:
            return AUTHENTICATION_CLOSE_CODE

        try:
            group = Group.objects.get(pk=self.group_id, is_active=True, is_deleted=False)
        except Group.DoesNotExist:
            return AUTHORIZATION_CLOSE_CODE

        if not ChatPermissionService.can_access_group_chat(user, group):
            return AUTHORIZATION_CLOSE_CODE
        return None

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
