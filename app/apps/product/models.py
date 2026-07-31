from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel


class MobilePlatform(models.TextChoices):
    ANDROID = "android", "Android"
    IOS = "ios", "iOS"


class UpdateMode(models.TextChoices):
    NONE = "none", "None"
    RECOMMENDED = "recommended", "Recommended"
    REQUIRED = "required", "Required"


class MobileAppReleasePolicy(BaseModel):
    """The only server-authoritative application-version policy."""

    platform = models.CharField(max_length=16, choices=MobilePlatform.choices)
    minimum_supported_version = models.CharField(max_length=32, blank=True)
    minimum_supported_build = models.PositiveIntegerField(default=0)
    latest_version = models.CharField(max_length=32)
    latest_build = models.PositiveIntegerField(default=0)
    update_mode = models.CharField(max_length=16, choices=UpdateMode.choices, default=UpdateMode.NONE)
    store_url = models.URLField(blank=True)
    message_ar = models.TextField(blank=True)
    message_en = models.TextField(blank=True)
    grace_period_ends_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    emergency_update_bypass = models.BooleanField(default=False)
    updated_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="updated_release_policies"
    )

    class Meta:
        ordering = ["platform", "-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["platform"], condition=models.Q(is_active=True), name="product_one_active_release_per_platform"
            )
        ]
        indexes = [models.Index(fields=["platform", "is_active"], name="prod_release_platform_idx")]

    def requires_update_for(self, build_number: int) -> bool:
        return bool(
            self.is_active
            and not self.emergency_update_bypass
            and self.update_mode == UpdateMode.REQUIRED
            and build_number < self.minimum_supported_build
            and (self.grace_period_ends_at is None or self.grace_period_ends_at <= timezone.now())
        )


class MaintenanceMode(BaseModel):
    """A time-bounded maintenance window controlled through the dashboard."""

    enabled = models.BooleanField(default=False)
    title_ar = models.CharField(max_length=255, blank=True)
    title_en = models.CharField(max_length=255, blank=True)
    message_ar = models.TextField(blank=True)
    message_en = models.TextField(blank=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    estimated_end_at = models.DateTimeField(null=True, blank=True)
    allowed_roles = models.JSONField(default=list, blank=True)
    bypass_staff = models.BooleanField(default=True)
    bypass_healthchecks = models.BooleanField(default=True)
    retry_after_seconds = models.PositiveIntegerField(default=300)
    updated_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="updated_maintenance_modes"
    )

    class Meta:
        ordering = ["-updated_at"]
        indexes = [models.Index(fields=["enabled", "starts_at", "ends_at"], name="prod_maintenance_window_idx")]

    @property
    def is_currently_active(self) -> bool:
        now = timezone.now()
        return bool(
            self.enabled
            and (self.starts_at is None or self.starts_at <= now)
            and (self.ends_at is None or self.ends_at > now)
        )


class FeatureFlag(BaseModel):
    key = models.SlugField(max_length=100)
    enabled = models.BooleanField(default=False)
    platform = models.CharField(max_length=16, choices=MobilePlatform.choices, blank=True)
    role = models.CharField(max_length=32, blank=True)
    expose_to_mobile = models.BooleanField(default=False)
    description = models.CharField(max_length=255, blank=True)
    updated_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="updated_feature_flags"
    )

    class Meta:
        ordering = ["key", "platform", "role"]
        constraints = [
            models.UniqueConstraint(fields=["key", "platform", "role"], name="product_feature_flag_scope_uniq")
        ]
        indexes = [models.Index(fields=["key", "enabled"], name="prod_feature_key_enabled_idx")]


class DeviceInstallation(BaseModel):
    user = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="device_installations"
    )
    installation_id = models.UUIDField(unique=True)
    platform = models.CharField(max_length=16, choices=MobilePlatform.choices)
    app_version = models.CharField(max_length=32, blank=True)
    build_number = models.PositiveIntegerField(default=0)
    push_token = models.CharField(max_length=512, null=True, blank=True, unique=True)
    locale = models.CharField(max_length=16, blank=True)
    timezone_name = models.CharField(max_length=64, blank=True)
    device_model = models.CharField(max_length=80, blank=True)
    last_seen_at = models.DateTimeField(default=timezone.now)
    notifications_enabled = models.BooleanField(default=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_seen_at"]
        indexes = [
            models.Index(fields=["user", "revoked_at"], name="prod_install_user_revoked_idx"),
            models.Index(fields=["platform", "last_seen_at"], name="prod_install_platform_seen_idx"),
        ]

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None


class TermsVersion(BaseModel):
    version = models.CharField(max_length=64, unique=True)
    content_url = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)
    requires_reacceptance = models.BooleanField(default=False)
    published_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-published_at"]


class PrivacyPolicyVersion(BaseModel):
    version = models.CharField(max_length=64, unique=True)
    content_url = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)
    requires_reacceptance = models.BooleanField(default=False)
    published_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-published_at"]


class ConsentKind(models.TextChoices):
    TERMS = "terms", "Terms"
    PRIVACY = "privacy", "Privacy"


class UserConsent(BaseModel):
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="consents")
    kind = models.CharField(max_length=16, choices=ConsentKind.choices)
    version = models.CharField(max_length=64)
    locale = models.CharField(max_length=16, blank=True)
    accepted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-accepted_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "kind", "version"], name="product_consent_user_kind_version_uniq")
        ]
        indexes = [models.Index(fields=["user", "kind", "accepted_at"], name="prod_consent_user_kind_idx")]


class AccountDeletionStatus(models.TextChoices):
    REQUESTED = "requested", "Requested"
    CANCELLED = "cancelled", "Cancelled"
    COMPLETED = "completed", "Completed"


class AccountDeletionRequest(BaseModel):
    user = models.OneToOneField("accounts.User", on_delete=models.CASCADE, related_name="account_deletion_request")
    status = models.CharField(
        max_length=16, choices=AccountDeletionStatus.choices, default=AccountDeletionStatus.REQUESTED
    )
    requested_at = models.DateTimeField(default=timezone.now)
    scheduled_for = models.DateTimeField()
    cancelled_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    reason = models.CharField(max_length=500, blank=True)

    class Meta:
        indexes = [models.Index(fields=["status", "scheduled_for"], name="prod_delete_status_sched_idx")]

    @classmethod
    def default_scheduled_for(cls):
        return timezone.now() + timedelta(days=getattr(settings, "ACCOUNT_DELETION_GRACE_DAYS", 30))


class IdempotencyRecord(BaseModel):
    """Database-backed replay store for sensitive API writes.

    The record has no secrets: keys and request bodies are SHA-256 digests only.
    """

    actor_key = models.CharField(max_length=80)
    endpoint = models.CharField(max_length=255)
    key_hash = models.CharField(max_length=64)
    payload_hash = models.CharField(max_length=64)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    response_body = models.JSONField(null=True, blank=True)
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["actor_key", "endpoint", "key_hash"], name="product_idempotency_scope_key_uniq"
            )
        ]
        indexes = [models.Index(fields=["expires_at"], name="prod_idempotency_expiry_idx")]
