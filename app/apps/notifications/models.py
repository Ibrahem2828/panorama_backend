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
    title_ar = models.CharField(max_length=255, blank=True)
    title_en = models.CharField(max_length=255, blank=True)
    body_ar = models.TextField(blank=True)
    body_en = models.TextField(blank=True)
    type = models.CharField(max_length=32, choices=NotificationType.choices, default=NotificationType.SYSTEM)
    data = models.JSONField(default=dict, blank=True)
    deep_link = models.CharField(max_length=255, blank=True)
    related_object_type = models.CharField(max_length=100, blank=True)
    related_object_id = models.CharField(max_length=64, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    deduplication_key = models.CharField(max_length=128, blank=True)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "is_read"]),
            models.Index(fields=["type", "created_at"]),
            models.Index(fields=["user", "expires_at"], name="notif_user_expiry_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "deduplication_key"],
                condition=~models.Q(deduplication_key=""),
                name="notif_user_deduplication_uniq",
            )
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


class NotificationPreference(BaseModel):
    user = models.OneToOneField("accounts.User", on_delete=models.CASCADE, related_name="notification_preference")
    in_app_enabled = models.BooleanField(default=True)
    push_enabled = models.BooleanField(default=True)
    disabled_types = models.JSONField(default=list, blank=True)

    def allows(self, notification_type: str) -> bool:
        return self.in_app_enabled and notification_type not in set(self.disabled_types)
