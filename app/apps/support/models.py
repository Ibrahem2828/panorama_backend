from __future__ import annotations

import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel


class SupportTicketCategory(models.TextChoices):
    ACCOUNT = "account", "Account"
    VERIFICATION = "verification", "Verification"
    PRINTING = "printing", "Printing"
    GROUPS = "groups", "Groups"
    FILES = "files", "Files"
    TECHNICAL = "technical", "Technical"
    SUGGESTION = "suggestion", "Suggestion"
    OTHER = "other", "Other"


class SupportTicketStatus(models.TextChoices):
    OPEN = "open", "Open"
    IN_PROGRESS = "in_progress", "In Progress"
    WAITING_USER = "waiting_user", "Waiting User"
    RESOLVED = "resolved", "Resolved"
    CLOSED = "closed", "Closed"


class SupportTicketPriority(models.TextChoices):
    LOW = "low", "Low"
    NORMAL = "normal", "Normal"
    HIGH = "high", "High"
    URGENT = "urgent", "Urgent"


class SupportTicket(BaseModel):
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="support_tickets")
    category = models.CharField(max_length=32, choices=SupportTicketCategory.choices, default=SupportTicketCategory.OTHER)
    subject = models.CharField(max_length=255)
    status = models.CharField(max_length=32, choices=SupportTicketStatus.choices, default=SupportTicketStatus.OPEN)
    priority = models.CharField(max_length=32, choices=SupportTicketPriority.choices, default=SupportTicketPriority.NORMAL)
    assigned_to = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, related_name="assigned_support_tickets", null=True, blank=True
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    last_response_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["status", "priority", "created_at"]),
            models.Index(fields=["assigned_to", "status"]),
        ]

    def close_if_needed(self):
        if self.status in {SupportTicketStatus.RESOLVED, SupportTicketStatus.CLOSED} and not self.closed_at:
            self.closed_at = timezone.now()
        elif self.status not in {SupportTicketStatus.RESOLVED, SupportTicketStatus.CLOSED}:
            self.closed_at = None

    def __str__(self) -> str:
        return self.subject


class SupportTicketMessage(BaseModel):
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="support_messages")
    message = models.TextField()
    attachment = models.FileField(upload_to="support/%Y/%m/", null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["ticket", "created_at"])]


class SupportAttachmentAccessTicket(BaseModel):
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    message = models.ForeignKey(SupportTicketMessage, on_delete=models.CASCADE, related_name="access_tickets")
    requested_by = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="support_attachment_access_tickets"
    )
    expires_at = models.DateTimeField()
    max_uses = models.PositiveSmallIntegerField(default=4)
    use_count = models.PositiveSmallIntegerField(default=0)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["token", "expires_at"], name="support_attachment_ticket_idx")]

    @property
    def is_valid(self):
        return (
            self.revoked_at is None
            and self.expires_at > timezone.now()
            and self.use_count < self.max_uses
            and not self.message.is_deleted
            and not self.message.ticket.is_deleted
        )

    @classmethod
    def issue(cls, message, requested_by):
        return cls.objects.create(
            message=message,
            requested_by=requested_by,
            expires_at=timezone.now() + timedelta(seconds=settings.FILE_ACCESS_TICKET_TTL_SECONDS),
            max_uses=min(settings.FILE_ACCESS_TICKET_MAX_USES, 4),
        )
