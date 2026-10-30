"""Notifications for new chat messages: grouped per chat, muted per member, never sent to someone already reading."""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.groups.models import GroupMembershipStatus
from apps.notifications.models import Notification, NotificationPreference, NotificationType
from apps.notifications.services import NotificationService, _push_data, push_channels, push_suppressed_reason

from .models import GroupChatPreference, Message, MessageType
from .presence import is_present
from .titles import chat_title

logger = logging.getLogger(__name__)

PREVIEW_CHARS = 90
#: At most one push per member per chat in this window, however many messages arrive: the in-app notification keeps
#: counting, so a rapid burst folds into one push while spaced messages each ring. Overridable at runtime with the
#: ``CHAT_PUSH_INTERVAL_SECONDS`` setting (0 pushes every message).
PUSH_INTERVAL_SECONDS = 8


def push_interval_seconds() -> int:
    try:
        return max(0, int(getattr(settings, "CHAT_PUSH_INTERVAL_SECONDS", PUSH_INTERVAL_SECONDS)))
    except (TypeError, ValueError):
        return PUSH_INTERVAL_SECONDS


def _preview(message: Message) -> str:
    if message.message_type == MessageType.IMAGE:
        return "أرسل صورة"
    if message.message_type == MessageType.FILE:
        return "أرسل ملفًا"
    text = " ".join(message.content.split())
    return text if len(text) <= PREVIEW_CHARS else text[: PREVIEW_CHARS - 1] + "…"


class ChatNotificationService:
    @staticmethod
    def recipients(message: Message) -> list[User]:
        return list(
            User.objects.filter(
                group_memberships__group=message.group,
                group_memberships__status=GroupMembershipStatus.APPROVED,
                is_active=True,
                is_deleted=False,
            )
            .exclude(pk=message.sender_id)
            .distinct()
        )

    @classmethod
    def notify(cls, message_id: int) -> int:
        """Returns how many members were notified (muted members and members reading the chat are skipped)."""

        message = Message.objects.select_related("sender", "group").filter(pk=message_id, is_deleted=False).first()
        if message is None or not message.group.is_active or message.group.is_deleted:
            return 0
        label = chat_title(message.sender) or message.sender.full_name
        preview = _preview(message)
        now = timezone.now()
        muted = set(
            GroupChatPreference.objects.filter(group=message.group, is_muted=True)
            .filter(Q(muted_until__isnull=True) | Q(muted_until__gt=now))  # an expired timed mute is not a mute
            .values_list("user_id", flat=True)
        )
        sent = 0
        for user in cls.recipients(message):
            if user.pk in muted or is_present(message.group_id, user.pk):
                continue
            try:
                if cls._notify_user(user, message, label, preview):
                    sent += 1
            except Exception:  # one member's failure must never stop the others
                logger.exception("chat_notification_failed", extra={"message_id": message.pk, "user_id": user.pk})
        return sent

    @classmethod
    def _notify_user(cls, user: User, message: Message, label: str, preview: str) -> bool:
        group = message.group
        preference, _created = NotificationPreference.objects.get_or_create(user=user)
        if not preference.allows(NotificationType.CHAT):
            return False
        if group.subject_id is None and preference.mute_communities:
            return False  # communities (no subject) switched off as a whole: same as muting each one, nothing is kept
        key = f"chat:{group.pk}"
        data = {
            "kind": "chat",
            "group_id": group.pk,
            "message_id": message.pk,
            "count": 1,
            "channel_id": "chat",
        }
        deep_link = f"/groups/{group.pk}/chat"
        existing = Notification.objects.filter(user=user, deduplication_key=key, is_deleted=False).first()
        if existing is not None and not existing.is_read:
            count = int(existing.data.get("count", 1)) + 1
            body = f"{count} رسائل جديدة. آخرها من {label}: {preview}"
            existing.data = {**existing.data, "count": count, "message_id": message.pk}
            existing.body = body
            existing.created_at = timezone.now()  # bubbles up to the top of the list
            existing.save(update_fields=["data", "body", "created_at", "updated_at"])
            cls._push(user, preference, existing, group.name, body)
            return True
        if existing is not None:
            # A read notification keeps its place in the history but gives up the key, so the next message makes a new one.
            existing.deduplication_key = ""
            existing.save(update_fields=["deduplication_key", "updated_at"])
        NotificationService.create_notification(
            user,
            group.name,
            f"{label}: {preview}",
            NotificationType.CHAT,
            data=data,
            deduplication_key=key,
            deep_link=deep_link,
            related_object_type="group",
            related_object_id=str(group.pk),
        )
        interval = push_interval_seconds()
        if interval:
            cache.set(f"chat:push:{group.pk}:{user.pk}", True, timeout=interval)
        return True

    @staticmethod
    def _push(
        user: User, preference: NotificationPreference, notification: Notification, title: str, body: str
    ) -> None:
        channels = push_channels()
        if not channels or push_suppressed_reason(preference, "chat", user.pk) is not None:
            return  # push off, chat channel off or quiet hours: the in-app notification is kept
        interval = push_interval_seconds()
        if interval and not cache.add(
            f"chat:push:{notification.data.get('group_id')}:{user.pk}", True, timeout=interval
        ):
            return  # a push for this chat went out a moment ago
        from apps.notifications.tasks import deliver_push_notification

        push_data = _push_data(notification, notification.data)
        transaction.on_commit(
            lambda: deliver_push_notification.delay(user.id, title, body, push_data, channels=channels), robust=True
        )
