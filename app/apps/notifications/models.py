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
    #: What the inbox groups by (see ``categories.py``); derived from the type and ``data["kind"]`` when created.
    category = models.CharField(max_length=24, blank=True, default="")
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
            models.Index(fields=["user", "category", "is_read"], name="notif_user_category_idx"),
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
    #: Per-channel push switches ``{"chat": false}``; a channel that is not listed is on.
    channels = models.JSONField(default=dict, blank=True)
    #: Quiet hours: pushes are held back (the in-app notification is still created) between start and end.
    quiet_enabled = models.BooleanField(default=False)
    quiet_start = models.TimeField(null=True, blank=True)
    quiet_end = models.TimeField(null=True, blank=True)
    quiet_timezone = models.CharField(max_length=64, default="Asia/Damascus")

    def allows(self, notification_type: str) -> bool:
        return self.in_app_enabled and notification_type not in set(self.disabled_types)

    def channel_enabled(self, channel: str | None) -> bool:
        return channel is None or bool(self.channels.get(channel, True))

    def in_quiet_hours(self, now=None) -> bool:
        """True while the member asked not to be pushed. The window may cross midnight (22:00 to 07:00)."""

        if not self.quiet_enabled or self.quiet_start is None or self.quiet_end is None:
            return False
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            zone = ZoneInfo(self.quiet_timezone)
        except (ZoneInfoNotFoundError, ValueError):
            zone = ZoneInfo("Asia/Damascus")
        local = (now or timezone.now()).astimezone(zone).time().replace(second=0, microsecond=0)
        start, end = self.quiet_start, self.quiet_end
        if start == end:
            return False
        if start < end:
            return start <= local < end
        return local >= start or local < end


class WebPushSubscription(BaseModel):
    """A browser (PWA) Web Push subscription. Deliberately separate from ``DeviceToken`` (Expo/mobile).

    ``endpoint`` identifies the subscription and is unique across users: a browser profile that signs in as
    another account simply re-assigns the row. ``p256dh``/``auth`` are the subscription's public encryption
    material and are never logged or returned by the API.
    """

    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="web_push_subscriptions")
    endpoint = models.URLField(max_length=1024, unique=True)
    p256dh = models.CharField(max_length=255)
    auth = models.CharField(max_length=255)
    user_agent = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    failure_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [models.Index(fields=["user", "is_active"], name="webpush_user_active_idx")]

    def __str__(self) -> str:
        return f"{self.user_id} - web push"


class PushDeliveryStatus(models.TextChoices):
    SENT = "sent", "Accepted by the push service"
    DELIVERED = "delivered", "Delivered (receipt ok)"
    FAILED = "failed", "Failed"
    DEAD = "dead", "Device is gone"


class PushDelivery(BaseModel):
    """One push handed to a provider, kept so delivery can be measured and receipts checked later.

    ``token`` is only kept while the receipt is pending (it is needed to switch a dead device off) and is blanked once
    the outcome is known; ``token_hash`` stays for counting distinct devices.
    """

    notification = models.ForeignKey(
        Notification, on_delete=models.SET_NULL, null=True, blank=True, related_name="deliveries"
    )
    channel = models.CharField(max_length=16, default="expo")
    category = models.CharField(max_length=24, blank=True, default="")
    token = models.CharField(max_length=512, blank=True)
    token_hash = models.CharField(max_length=64, blank=True)
    ticket_id = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=16, choices=PushDeliveryStatus.choices, default=PushDeliveryStatus.SENT)
    error_code = models.CharField(max_length=64, blank=True)
    attempts = models.PositiveSmallIntegerField(default=1)
    receipt_checked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"], name="pushdelivery_status_idx"),
            models.Index(fields=["channel", "created_at"], name="pushdelivery_channel_idx"),
        ]
