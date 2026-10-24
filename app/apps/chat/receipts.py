"""Message ticks (sent / delivered / read) and unread counters, built on one cursor per member per group."""

from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.db.models import Max

from apps.groups.models import GroupMembershipStatus

from .models import GroupReadState, Message
from .services import ChatBroadcastService, ChatPermissionService


@dataclass(frozen=True)
class Delivery:
    state: str  # "sent" | "delivered" | "read"
    delivered_count: int
    read_count: int
    recipients: int

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "delivered_count": self.delivered_count,
            "read_count": self.read_count,
            "recipients": self.recipients,
        }


class ChatReceiptService:
    """A message shows one tick when it reached the server, two grey ticks when at least one other member's device has it,
    and two blue ticks when at least one other member read it. The exact counts ride along for the message details."""

    @staticmethod
    def latest_message_id(group) -> int:
        return Message.objects.filter(group=group).aggregate(top=Max("id"))["top"] or 0

    @classmethod
    @transaction.atomic
    def update(
        cls, *, group, user, delivered_up_to: int | None = None, read_up_to: int | None = None
    ) -> GroupReadState:
        ChatPermissionService.enforce_group_chat_access(user, group)
        latest = cls.latest_message_id(group)
        delivered = min(max(int(delivered_up_to or 0), 0), latest)
        read = min(max(int(read_up_to or 0), 0), latest)
        state, _created = GroupReadState.objects.select_for_update().get_or_create(group=group, user=user)
        new_read = max(state.read_up_to, read)
        new_delivered = max(state.delivered_up_to, delivered, new_read)  # a message that was read has certainly arrived
        if (new_read, new_delivered) != (state.read_up_to, state.delivered_up_to):
            state.read_up_to = new_read
            state.delivered_up_to = new_delivered
            state.save(update_fields=["read_up_to", "delivered_up_to", "updated_at"])
            ChatBroadcastService.after_commit_receipt(group.pk, user.pk, new_delivered, new_read)
        return state

    @staticmethod
    def cursors(group) -> list[tuple[int, int, int]]:
        """(user_id, delivered_up_to, read_up_to) of every member that has opened the chat."""

        return list(GroupReadState.objects.filter(group=group).values_list("user_id", "delivered_up_to", "read_up_to"))

    @staticmethod
    def member_count(group) -> int:
        return group.memberships.filter(status=GroupMembershipStatus.APPROVED).count()

    @staticmethod
    def delivery_for(message_id: int, sender_id: int, cursors: list[tuple[int, int, int]], recipients: int) -> Delivery:
        delivered = sum(1 for user_id, d, _r in cursors if user_id != sender_id and d >= message_id)
        read = sum(1 for user_id, _d, r in cursors if user_id != sender_id and r >= message_id)
        state = "read" if read else "delivered" if delivered else "sent"
        return Delivery(state=state, delivered_count=delivered, read_count=read, recipients=max(recipients - 1, 0))


class ChatUnreadService:
    @staticmethod
    def counts(user) -> list[dict]:
        """Unread messages per group the user belongs to (their own messages never count)."""

        group_ids = list(
            user.group_memberships.filter(status=GroupMembershipStatus.APPROVED, group__is_deleted=False).values_list(
                "group_id", flat=True
            )
        )
        cursors = dict(
            GroupReadState.objects.filter(group_id__in=group_ids, user=user).values_list("group_id", "read_up_to")
        )
        # A student belongs to a handful of groups, so one small aggregate per group keeps the numbers obviously right.
        return [
            {
                "group_id": group_id,
                "unread": Message.objects.filter(group_id=group_id, is_deleted=False, id__gt=cursors.get(group_id, 0))
                .exclude(sender=user)
                .count(),
            }
            for group_id in group_ids
        ]
