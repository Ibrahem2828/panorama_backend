from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel


class NotificationType(models.TextChoices):
    VERIFICATION = "verification", "Verification"
    GROUP = "group", "Group"
    FILE = "file", "File"
    ANNOUNCEMENT = "announcement", "Announcement"
    SYSTEM = "system", "System"
    PRINTING = "printing", "Printing"
    SUPPORT = "support", "Support"
    FEEDBACK = "feedback", "Feedback"
    CHAT = "chat", "Chat"


class Notification(BaseModel):
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="notifications")
    title = models.CharField(max_length=255)
    body = models.TextField()
    type = models.CharField(max_length=32, choices=NotificationType.choices, default=NotificationType.SYSTEM)
    data = models.JSONField(default=dict, blank=True)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "is_read"]),
            models.Index(fields=["type", "created_at"]),
        ]

    def mark_read(self) -> None:
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=["is_read", "read_at", "updated_at"])

    def __str__(self) -> str:
        return f"{self.user_id} - {self.title}"


class DevicePlatform(models.TextChoices):
    ANDROID = "android", "Android"
    IOS = "ios", "iOS"
    WEB = "web", "Web"


class DeviceToken(BaseModel):
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="device_tokens")
    token = models.CharField(max_length=512, unique=True)
    platform = models.CharField(max_length=16, choices=DevicePlatform.choices)
    is_active = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["token"]),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} - {self.platform}"
