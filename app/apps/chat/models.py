from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel


class MessageType(models.TextChoices):
    TEXT = "text", "Text"
    IMAGE = "image", "Image"
    FILE = "file", "File"
    SYSTEM = "system", "System"


class Message(BaseModel):
    group = models.ForeignKey("groups.Group", on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="chat_messages")
    content = models.TextField(blank=True)
    message_type = models.CharField(max_length=32, choices=MessageType.choices, default=MessageType.TEXT)
    attachment = models.FileField(upload_to="chat/", null=True, blank=True)
    reply_to = models.ForeignKey("self", on_delete=models.SET_NULL, related_name="replies", null=True, blank=True)
    deleted_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        related_name="deleted_chat_messages",
        null=True,
        blank=True,
    )
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["group", "created_at"]),
            models.Index(fields=["sender", "created_at"]),
        ]

    def soft_delete(self, user):
        self.is_deleted = True
        self.deleted_by = user
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_deleted", "deleted_by", "deleted_at", "updated_at"])


class MessageAttachmentAccessTicket(BaseModel):
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="attachment_access_tickets")
    requested_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="chat_attachment_access_tickets",
    )
    expires_at = models.DateTimeField()
    max_uses = models.PositiveSmallIntegerField(default=8)
    use_count = models.PositiveSmallIntegerField(default=0)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["token", "expires_at"], name="chat_messag_token_3efcbb_idx")]

    @property
    def is_valid(self) -> bool:
        return (
            self.revoked_at is None
            and self.expires_at > timezone.now()
            and self.use_count < self.max_uses
            and not self.message.is_deleted
            and bool(self.message.attachment)
        )

    @classmethod
    def issue(cls, message, requested_by):
        from datetime import timedelta

        return cls.objects.create(
            message=message,
            requested_by=requested_by,
            expires_at=timezone.now() + timedelta(seconds=settings.FILE_ACCESS_TICKET_TTL_SECONDS),
            max_uses=settings.FILE_ACCESS_TICKET_MAX_USES,
        )


class MessageReportStatus(models.TextChoices):
    OPEN = "open", "Open"
    REVIEWED = "reviewed", "Reviewed"  # legacy value, still treated as unresolved
    DISMISSED = "dismissed", "Dismissed"
    MESSAGE_DELETED = "message_deleted", "Message deleted"


UNRESOLVED_REPORT_STATUSES = (MessageReportStatus.OPEN, MessageReportStatus.REVIEWED)


class MessageReport(BaseModel):
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="reports")
    reported_by = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="message_reports")
    reason = models.TextField()
    status = models.CharField(max_length=32, choices=MessageReportStatus.choices, default=MessageReportStatus.OPEN)
    resolved_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, related_name="resolved_message_reports", null=True, blank=True
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["message", "reported_by"], name="unique_message_report_per_user")
        ]
        indexes = [
            models.Index(fields=["message", "reported_by"]),
            models.Index(fields=["status", "created_at"], name="chat_report_status_idx"),
        ]


class GroupReadState(BaseModel):
    """How far one member has received and read a group's chat (a cursor, not one row per message).

    ``delivered_up_to`` / ``read_up_to`` are message ids: everything at or below them has reached / been read by this
    member. A message's ticks are derived from the other members' cursors, so a 500-member group costs 500 rows in total,
    not 500 rows for every message.
    """

    group = models.ForeignKey("groups.Group", on_delete=models.CASCADE, related_name="read_states")
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="chat_read_states")
    delivered_up_to = models.PositiveBigIntegerField(default=0)
    read_up_to = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["group", "user"], name="chat_read_state_unique")]
        indexes = [models.Index(fields=["group", "user"], name="chat_read_state_lookup_idx")]


#: One reaction per member per message (changing it replaces the previous one), chosen from this fixed set.
ALLOWED_REACTIONS = ("👍", "❤️", "😂", "😮", "😢", "🙏")


class MessageReaction(BaseModel):
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="reactions")
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="chat_reactions")
    emoji = models.CharField(max_length=16)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["message", "user"], name="chat_reaction_one_per_member")]
        indexes = [models.Index(fields=["message"], name="chat_reaction_message_idx")]


class GroupChatPreference(BaseModel):
    """A member's own setting for one group chat (today: mute its notifications)."""

    group = models.ForeignKey("groups.Group", on_delete=models.CASCADE, related_name="chat_preferences")
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="chat_preferences")
    is_muted = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["group", "user"], name="chat_preference_unique")]
