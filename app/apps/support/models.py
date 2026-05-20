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
    assigned_to = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, related_name="assigned_support_tickets", null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

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

    def __str__(self) -> str:
        return self.subject


class SupportTicketMessage(BaseModel):
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="support_messages")
    message = models.TextField()
    attachment = models.FileField(upload_to="support/", null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["ticket", "created_at"])]
