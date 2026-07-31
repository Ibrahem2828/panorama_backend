from __future__ import annotations

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel


class FeedbackKind(models.TextChoices):
    RATING = "rating", "Rating"
    SUGGESTION = "suggestion", "Suggestion"
    BUG = "bug", "Bug Report"
    COMPLAINT = "complaint", "Complaint"
    COMPLIMENT = "compliment", "Compliment"


class FeedbackMetricType(models.TextChoices):
    CSAT = "csat", "Customer Satisfaction"
    CES = "ces", "Customer Effort Score"
    NPS = "nps", "Net Promoter Score"
    STARS = "stars", "Stars"
    FREE_TEXT = "free_text", "Free Text"


class FeedbackReleaseChannel(models.TextChoices):
    PRODUCTION = "production", "Production"
    BETA = "beta", "Beta"
    INTERNAL = "internal", "Internal"


class FeedbackContext(models.TextChoices):
    APP = "app", "Whole App"
    ONBOARDING = "onboarding", "Onboarding"
    REGISTRATION = "registration", "Registration"
    LOGIN = "login", "Login"
    VERIFICATION = "verification", "Student Verification"
    HOME = "home", "Home"
    SUBJECT = "subject", "Subject"
    GROUP = "group", "Group"
    CHAT = "chat", "Chat"
    FILE = "file", "File Viewer"
    PRINTING = "printing", "Printing"
    NOTIFICATION = "notification", "Notifications"
    SUPPORT = "support", "Support"
    PROFILE = "profile", "Profile"
    SEARCH = "search", "Search"
    ANNOUNCEMENT = "announcement", "Announcement"
    SETTINGS = "settings", "Settings"
    EXTERNAL_CHANNEL = "external_channel", "External Channel"
    OTHER = "other", "Other"


class FeedbackStatus(models.TextChoices):
    NEW = "new", "New"
    REVIEWING = "reviewing", "Reviewing"
    PLANNED = "planned", "Planned"
    IN_PROGRESS = "in_progress", "In Progress"
    RESOLVED = "resolved", "Resolved"
    REJECTED = "rejected", "Rejected"
    DUPLICATE = "duplicate", "Duplicate"


class FeedbackPriority(models.TextChoices):
    LOW = "low", "Low"
    NORMAL = "normal", "Normal"
    HIGH = "high", "High"
    CRITICAL = "critical", "Critical"


class FeedbackPromptPolicy(BaseModel):
    context = models.CharField(max_length=32, choices=FeedbackContext.choices)
    action_key = models.CharField(max_length=100, blank=True)
    title = models.CharField(max_length=200)
    question = models.CharField(max_length=500)
    is_active = models.BooleanField(default=True)
    minimum_app_version = models.CharField(max_length=32, blank=True)
    maximum_app_version = models.CharField(max_length=32, blank=True)
    cooldown_days = models.PositiveSmallIntegerField(default=30)
    dismiss_cooldown_days = models.PositiveSmallIntegerField(default=7)
    max_prompts_per_30_days = models.PositiveSmallIntegerField(default=1)
    sample_percent = models.PositiveSmallIntegerField(
        default=100,
        validators=[MinValueValidator(1), MaxValueValidator(100)],
    )
    allow_comment = models.BooleanField(default=True)
    allow_suggestion = models.BooleanField(default=True)
    platforms = models.JSONField(default=list, blank=True)
    locales = models.JSONField(default=list, blank=True)
    roles = models.JSONField(default=list, blank=True)
    verification_statuses = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["context", "action_key"]
        constraints = [models.UniqueConstraint(fields=["context", "action_key"], name="unique_feedback_prompt_policy")]

    def __str__(self):
        return f"{self.context}:{self.action_key or '*'}"


class AppFeedback(BaseModel):
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="app_feedback")
    kind = models.CharField(max_length=32, choices=FeedbackKind.choices)
    metric_type = models.CharField(
        max_length=16,
        choices=FeedbackMetricType.choices,
        default=FeedbackMetricType.STARS,
    )
    metric_value = models.PositiveSmallIntegerField(null=True, blank=True)
    context = models.CharField(max_length=32, choices=FeedbackContext.choices)
    action_key = models.CharField(max_length=100, blank=True)
    object_type = models.CharField(max_length=100, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    rating = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    title = models.CharField(max_length=200, blank=True)
    comment = models.TextField(blank=True)
    suggestion = models.TextField(blank=True)
    status = models.CharField(max_length=32, choices=FeedbackStatus.choices, default=FeedbackStatus.NEW)
    priority = models.CharField(max_length=16, choices=FeedbackPriority.choices, default=FeedbackPriority.NORMAL)
    assigned_to = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        related_name="assigned_feedback",
        null=True,
        blank=True,
    )
    internal_notes = models.TextField(blank=True)
    resolution_message = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    app_version = models.CharField(max_length=32, blank=True)
    build_number = models.CharField(max_length=32, blank=True)
    platform = models.CharField(max_length=32, blank=True)
    locale = models.CharField(max_length=16, blank=True)
    release_channel = models.CharField(max_length=16, choices=FeedbackReleaseChannel.choices, blank=True)
    journey_id = models.CharField(max_length=100, blank=True)
    session_id = models.CharField(max_length=100, blank=True)
    experiment_key = models.CharField(max_length=100, blank=True)
    source_screen = models.CharField(max_length=100, blank=True)
    device_model = models.CharField(max_length=100, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    content_fingerprint = models.CharField(max_length=64, blank=True, db_index=True)
    abuse_flags = models.JSONField(default=list, blank=True)
    is_hidden = models.BooleanField(default=False)
    deletion_requested_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["context", "action_key", "created_at"], name="feedback_context_action_idx"),
            models.Index(fields=["kind", "status", "priority"], name="feedback_kind_status_idx"),
            models.Index(fields=["rating", "created_at"], name="feedback_rating_created_idx"),
            models.Index(fields=["user", "created_at"], name="feedback_user_created_idx"),
            models.Index(fields=["metric_type", "metric_value", "created_at"], name="feedback_metric_created_idx"),
            models.Index(fields=["platform", "app_version", "created_at"], name="feedback_version_created_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "context", "action_key", "object_type", "object_id", "app_version"],
                condition=models.Q(kind=FeedbackKind.RATING, is_deleted=False),
                name="unique_rating_per_action_object_version",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(metric_value__isnull=True)
                    | models.Q(
                        metric_type__in=[FeedbackMetricType.CSAT, FeedbackMetricType.STARS],
                        metric_value__gte=1,
                        metric_value__lte=5,
                    )
                    | models.Q(metric_type=FeedbackMetricType.CES, metric_value__gte=1, metric_value__lte=7)
                    | models.Q(metric_type=FeedbackMetricType.NPS, metric_value__gte=0, metric_value__lte=10)
                ),
                name="feedback_metric_value_in_range",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.status == FeedbackStatus.RESOLVED and self.resolved_at is None:
            self.resolved_at = timezone.now()
        elif self.status != FeedbackStatus.RESOLVED:
            self.resolved_at = None
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.kind}:{self.context}:{self.user_id}"


class FeedbackVote(BaseModel):
    feedback = models.ForeignKey(AppFeedback, on_delete=models.CASCADE, related_name="votes")
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="feedback_votes")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["feedback", "user"], name="unique_feedback_vote")]
        indexes = [models.Index(fields=["feedback", "created_at"], name="feedback_vote_created_idx")]


class FeedbackPromptEvent(BaseModel):
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="feedback_prompt_events")
    policy = models.ForeignKey(FeedbackPromptPolicy, on_delete=models.CASCADE, related_name="events")
    event = models.CharField(
        max_length=16,
        choices=(("shown", "Shown"), ("dismissed", "Dismissed"), ("submitted", "Submitted")),
    )
    app_version = models.CharField(max_length=32, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "policy", "event", "created_at"], name="feedback_prompt_event_idx")]


class FeedbackAITriageStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    COMPLETED = "completed", "Completed"
    SKIPPED = "skipped", "Skipped"
    FAILED = "failed", "Failed"
    HUMAN_CORRECTED = "human_corrected", "Human Corrected"


class FeedbackAITriage(BaseModel):
    """Optional, review-only classification produced from redacted feedback."""

    feedback = models.OneToOneField(AppFeedback, on_delete=models.CASCADE, related_name="ai_triage")
    status = models.CharField(
        max_length=24, choices=FeedbackAITriageStatus.choices, default=FeedbackAITriageStatus.QUEUED
    )
    provider = models.CharField(max_length=64, blank=True)
    model = models.CharField(max_length=128, blank=True)
    model_version = models.CharField(max_length=64, blank=True)
    confidence = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    topic = models.CharField(max_length=128, blank=True)
    sentiment = models.CharField(max_length=32, blank=True)
    suggested_priority = models.CharField(max_length=16, choices=FeedbackPriority.choices, blank=True)
    similar_feedback = models.ForeignKey(
        AppFeedback,
        on_delete=models.SET_NULL,
        related_name="similarity_references",
        null=True,
        blank=True,
    )
    redacted_text = models.TextField(blank=True)
    failure_reason = models.CharField(max_length=200, blank=True)
    reviewed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        related_name="reviewed_feedback_triages",
        null=True,
        blank=True,
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["status", "created_at"], name="feedback_ai_status_created_idx")]
