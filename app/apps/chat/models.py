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
    deleted_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, related_name="deleted_chat_messages", null=True, blank=True)
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
        indexes = [models.Index(fields=["message", "reported_by"])]
