"""Emoji reactions on chat messages: one per member per message, chosen from a small fixed set."""

from __future__ import annotations

from collections import defaultdict

from django.db import transaction
from rest_framework.exceptions import ValidationError

from .models import ALLOWED_REACTIONS, Message, MessageReaction
from .services import ChatBroadcastService, ChatPermissionService


class ChatReactionService:
    @staticmethod
    def counts(message_ids: list[int]) -> dict[int, dict[str, int]]:
        result: dict[int, dict[str, int]] = defaultdict(dict)
        for message_id, emoji in MessageReaction.objects.filter(message_id__in=message_ids).values_list(
            "message_id", "emoji"
        ):
            result[message_id][emoji] = result[message_id].get(emoji, 0) + 1
        return result

    @staticmethod
    def mine(message_ids: list[int], user) -> dict[int, str]:
        return dict(
            MessageReaction.objects.filter(message_id__in=message_ids, user=user).values_list("message_id", "emoji")
        )

    @classmethod
    @transaction.atomic
    def react(cls, *, message: Message, user, emoji: str | None) -> dict[str, int]:
        """Set (or, with ``emoji=None``, clear) the member's reaction and announce the new totals after commit."""

        ChatPermissionService.enforce_group_chat_access(user, message.group)
        if message.is_deleted:
            raise ValidationError({"message": "This message is no longer available."})
        if emoji is None:
            MessageReaction.objects.filter(message=message, user=user).delete()
        else:
            if emoji not in ALLOWED_REACTIONS:
                raise ValidationError({"emoji": "This reaction is not available."})
            MessageReaction.objects.update_or_create(message=message, user=user, defaults={"emoji": emoji})
        counts = cls.counts([message.pk]).get(message.pk, {})
        ChatBroadcastService.after_commit_reaction(message.group_id, message.pk, user.pk, emoji, counts)
        return counts
