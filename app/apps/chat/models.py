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
    REVIEWED = "reviewed", "Reviewed"


class MessageReport(BaseModel):
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="reports")
    reported_by = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="message_reports")
    reason = models.TextField()
    status = models.CharField(max_length=32, choices=MessageReportStatus.choices, default=MessageReportStatus.OPEN)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["message", "reported_by"], name="unique_message_report_per_user")
        ]
        indexes = [models.Index(fields=["message", "reported_by"])]
