from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from secrets import token_urlsafe

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from redis.exceptions import RedisError
from rest_framework import status
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError

from apps.accounts.choices import StudentVerificationStatus
from apps.accounts.models import User
from apps.accounts.permissions import Capability, PermissionService
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.groups.models import Group, GroupMembershipRole, GroupMembershipStatus

from .models import Message, MessageType

logger = logging.getLogger(__name__)


class ChatPermissionService:
    @staticmethod
    def can_access_group_chat(user, group) -> bool:
        if not user or not user.is_authenticated or not user.is_active or group.is_deleted:
            return False
        if PermissionService.has(user, Capability.GROUPS_MANAGE):
            return True
        profile = getattr(user, "student_profile", None)
        if not profile or profile.verification_status != StudentVerificationStatus.APPROVED:
            return False
        return user.group_memberships.filter(group=group, status=GroupMembershipStatus.APPROVED).exists()

    @staticmethod
    def get_membership(user, group):
        if not user or not user.is_authenticated:
            return None
        return user.group_memberships.filter(group=group, status=GroupMembershipStatus.APPROVED).first()

    @staticmethod
    def can_send_message(user, group) -> bool:
        if not ChatPermissionService.can_access_group_chat(user, group):
            return False
        if PermissionService.has(user, Capability.GROUPS_MANAGE):
            return True
        membership = ChatPermissionService.get_membership(user, group)
        if not membership:
            return False
        if group.send_messages_permission == "all_members":
            return True
        return membership.role in {GroupMembershipRole.MODERATOR, GroupMembershipRole.GROUP_ADMIN}

    @staticmethod
    def can_moderate_messages(user, group) -> bool:
        if PermissionService.has(user, Capability.GROUPS_MANAGE):
            return True
        membership = ChatPermissionService.get_membership(user, group)
        return bool(membership and membership.role in {GroupMembershipRole.MODERATOR, GroupMembershipRole.GROUP_ADMIN})

    @staticmethod
    def enforce_group_chat_access(user, group):
        if not ChatPermissionService.can_access_group_chat(user, group):
            raise PermissionDenied("You are not allowed to access this group chat.")

    @staticmethod
    def enforce_can_send_message(user, group):
        if not ChatPermissionService.can_send_message(user, group):
            raise PermissionDenied("You are not allowed to send messages in this group.")


@dataclass(frozen=True)
class ChatHandshakeTicket:
    """The client-facing values for a one-time WebSocket handshake ticket."""

    token: str
    expires_at: datetime


class ChatTicketStorageUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "WebSocket authentication is temporarily unavailable."
    default_code = "chat_ticket_storage_unavailable"


class ChatHandshakeTicketService:
    """Issue and consume short-lived, opaque WebSocket handshake credentials.

    The credential is deliberately stored only in the configured shared cache.
    It contains no JWT material and it is consumed before the socket is accepted.
    Redis `add` reserves consumption atomically; the same semantics are retained by
    Django's local cache in tests.
    """

    PURPOSE = "group-chat-websocket-v1"
    CACHE_PREFIX = "chat:handshake:"
    CONSUMED_PREFIX = "chat:handshake:consumed:"

    @classmethod
    def _ttl_seconds(cls) -> int:
        return int(settings.WEBSOCKET_CHAT_TICKET_TTL_SECONDS)

    @classmethod
    def _cache_key(cls, token: str) -> str:
        return f"{cls.CACHE_PREFIX}{token}"

    @classmethod
    def _consumed_key(cls, token: str) -> str:
        return f"{cls.CONSUMED_PREFIX}{token}"

    @classmethod
    def issue(cls, *, user: User, group: Group) -> ChatHandshakeTicket:
        """Validate current authorization before issuing a handshake ticket."""

        if not user.is_active or user.is_deleted:
            raise PermissionDenied("Your account is not active.")
        if not group.is_active or group.is_deleted:
            raise PermissionDenied("This group chat is unavailable.")
        ChatPermissionService.enforce_group_chat_access(user, group)

        token = token_urlsafe(32)
        ttl_seconds = cls._ttl_seconds()
        expires_at = timezone.now() + timedelta(seconds=ttl_seconds)
        payload = {
            "purpose": cls.PURPOSE,
            "user_id": user.pk,
            "group_id": group.pk,
            "session_version": user.session_version,
        }
        try:
            cache.set(cls._cache_key(token), payload, timeout=ttl_seconds)
        except (OSError, RedisError, TimeoutError) as exc:
            raise ChatTicketStorageUnavailable() from exc
        return ChatHandshakeTicket(token=token, expires_at=expires_at)

    @classmethod
    def consume(cls, token: str, *, group_id: int) -> dict[str, int] | None:
        """Atomically reserve a ticket for the requested group and invalidate it."""

        if not token or len(token) > 256:
            return None
        try:
            payload = cache.get(cls._cache_key(token))
            if not isinstance(payload, dict):
                return None
            if (
                payload.get("purpose") != cls.PURPOSE
                or payload.get("group_id") != group_id
                or not isinstance(payload.get("user_id"), int)
                or not isinstance(payload.get("session_version"), int)
            ):
                return None
            # A successful add is the one winner in a concurrent handshake race.
            if not cache.add(cls._consumed_key(token), True, timeout=cls._ttl_seconds()):
                return None
            cache.delete(cls._cache_key(token))
        except (OSError, RedisError, TimeoutError):
            return None
        return {
            "user_id": payload["user_id"],
            "group_id": payload["group_id"],
            "session_version": payload["session_version"],
        }

    @classmethod
    def authenticate_claims(cls, claims: dict[str, int], *, group_id: int) -> User | None:
        """Recheck all mutable authorization state immediately before accept."""

        if claims.get("group_id") != group_id:
            return None
        user = User.objects.filter(pk=claims["user_id"], is_active=True, is_deleted=False).first()
        group = Group.objects.filter(pk=group_id, is_active=True, is_deleted=False).first()
        if user is None or group is None or user.session_version != claims["session_version"]:
            return None
        return user if ChatPermissionService.can_access_group_chat(user, group) else None


def chat_group_name(group_id: int) -> str:
    return f"group_chat_{group_id}"


class ChatBroadcastService:
    """Push REST-side chat changes to the sockets connected through ``GroupChatConsumer``.

    Always called through ``transaction.on_commit`` so a rolled-back change is never announced. A channel-layer
    outage must not fail the request (clients still converge on the next REST refresh), so errors are logged only.
    """

    @staticmethod
    def _group_send(group_id: int, event: dict) -> None:
        try:
            layer = get_channel_layer()
            if layer is not None:
                async_to_sync(layer.group_send)(chat_group_name(group_id), event)
        except Exception:
            logger.exception("chat_broadcast_failed", extra={"group_id": group_id, "event_type": event.get("type")})

    @classmethod
    def message_created(cls, message_id: int) -> None:
        from .serializers import MessageSerializer

        message = Message.objects.select_related("sender").filter(pk=message_id, is_deleted=False).first()
        if message is not None:
            cls._group_send(message.group_id, {"type": "message_event", "data": MessageSerializer(message).data})

    @classmethod
    def message_deleted(cls, group_id: int, message_id: int) -> None:
        cls._group_send(group_id, {"type": "message.deleted", "data": {"id": message_id, "group_id": group_id}})

    @classmethod
    def receipt_updated(cls, group_id: int, user_id: int, delivered_up_to: int, read_up_to: int) -> None:
        cls._group_send(
            group_id,
            {
                "type": "receipt.updated",
                "data": {
                    "group_id": group_id,
                    "user_id": user_id,
                    "delivered_up_to": delivered_up_to,
                    "read_up_to": read_up_to,
                },
            },
        )

    @classmethod
    def reaction_updated(
        cls, group_id: int, message_id: int, user_id: int, emoji: str | None, counts: dict[str, int]
    ) -> None:
        cls._group_send(
            group_id,
            {
                "type": "reaction.updated",
                "data": {
                    "group_id": group_id,
                    "message_id": message_id,
                    "user_id": user_id,
                    "emoji": emoji,
                    "counts": counts,
                },
            },
        )

    @classmethod
    def after_commit_receipt(cls, group_id: int, user_id: int, delivered_up_to: int, read_up_to: int) -> None:
        transaction.on_commit(lambda: cls.receipt_updated(group_id, user_id, delivered_up_to, read_up_to))

    @classmethod
    def after_commit_reaction(
        cls, group_id: int, message_id: int, user_id: int, emoji: str | None, counts: dict[str, int]
    ) -> None:
        transaction.on_commit(lambda: cls.reaction_updated(group_id, message_id, user_id, emoji, counts))

    @classmethod
    def after_commit_created(cls, message: Message) -> None:
        transaction.on_commit(lambda: cls.message_created(message.pk))

    @classmethod
    def after_commit_deleted(cls, message: Message) -> None:
        group_id, message_id = message.group_id, message.pk
        transaction.on_commit(lambda: cls.message_deleted(group_id, message_id))


class ChatMessageService:
    @staticmethod
    def delete_message(message: Message, *, actor, request=None, extra_audit: dict | None = None) -> None:
        """Soft-delete, audit and (after commit) announce a deletion; the single path used by every caller."""

        with transaction.atomic():
            message.soft_delete(actor)
            AuditLogService.log(
                actor=actor,
                action=AuditAction.MESSAGE_DELETED,
                target=message,
                new_value=extra_audit,
                request=request,
                critical=True,
            )
            ChatBroadcastService.after_commit_deleted(message)

    @staticmethod
    def create_message(
        group,
        sender,
        content: str = "",
        message_type: str = MessageType.TEXT,
        attachment=None,
        reply_to=None,
    ) -> Message:
        ChatPermissionService.enforce_group_chat_access(sender, group)
        ChatPermissionService.enforce_can_send_message(sender, group)
        content = str(content or "").strip()
        if len(content) > 4000:
            raise ValidationError({"content": "Messages cannot exceed 4000 characters."})
        if message_type == MessageType.TEXT and not content:
            raise ValidationError({"content": "Content is required for text messages."})
        if message_type in {MessageType.IMAGE, MessageType.FILE} and not attachment:
            raise ValidationError({"attachment": "Attachment is required for file or image messages."})
        if message_type == MessageType.SYSTEM:
            raise ValidationError({"message_type": "System messages are server generated."})
        if reply_to and (reply_to.group_id != group.id or reply_to.is_deleted):
            raise ValidationError({"reply_to": "The referenced message is not available in this group."})
        message = Message.objects.create(
            group=group,
            sender=sender,
            content=content,
            message_type=message_type,
            attachment=attachment,
            reply_to=reply_to,
        )
        # Every path that creates a message (REST and WebSocket) tells the other members once it is committed.
        transaction.on_commit(lambda: ChatMessageService.queue_notification(message.pk))
        return message

    @staticmethod
    def queue_notification(message_id: int) -> None:
        """Hand the notification fan-out to the worker; a broker outage must never fail or delay the send."""

        try:
            from .tasks import notify_chat_message

            notify_chat_message.delay(message_id)
        except Exception:
            logger.exception("chat_notification_queue_failed", extra={"message_id": message_id})

    @staticmethod
    def create_and_announce(**kwargs) -> Message:
        """REST path: create the message and broadcast it once the transaction commits."""

        with transaction.atomic():
            message = ChatMessageService.create_message(**kwargs)
            ChatBroadcastService.after_commit_created(message)
        return message
