from __future__ import annotations

from rest_framework import serializers

from apps.accounts.choices import UserRole
from apps.accounts.models import User

from .models import (
    AppFeedback,
    FeedbackKind,
    FeedbackMetricType,
    FeedbackPriority,
    FeedbackPromptEvent,
    FeedbackPromptPolicy,
    FeedbackStatus,
)
from .services import FeedbackService, validate_metadata


class FeedbackPromptPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = FeedbackPromptPolicy
        fields = [
            "id",
            "context",
            "action_key",
            "title",
            "question",
            "is_active",
            "minimum_app_version",
            "maximum_app_version",
            "cooldown_days",
            "dismiss_cooldown_days",
            "max_prompts_per_30_days",
            "sample_percent",
            "allow_comment",
            "allow_suggestion",
            "platforms",
            "locales",
            "roles",
            "verification_statuses",
        ]
        read_only_fields = fields


class DashboardFeedbackPromptPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = FeedbackPromptPolicy
        fields = [
            "id",
            "context",
            "action_key",
            "title",
            "question",
            "is_active",
            "minimum_app_version",
            "maximum_app_version",
            "cooldown_days",
            "dismiss_cooldown_days",
            "max_prompts_per_30_days",
            "sample_percent",
            "allow_comment",
            "allow_suggestion",
            "platforms",
            "locales",
            "roles",
            "verification_statuses",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        for field in ("platforms", "locales", "roles", "verification_statuses"):
            values = attrs.get(field, getattr(self.instance, field, []))
            if not isinstance(values, list) or len(values) > 25 or any(not isinstance(value, str) for value in values):
                raise serializers.ValidationError({field: "Use a list of at most 25 strings."})
        minimum = attrs.get("minimum_app_version", getattr(self.instance, "minimum_app_version", ""))
        maximum = attrs.get("maximum_app_version", getattr(self.instance, "maximum_app_version", ""))
        def version_tuple(value):
            return tuple(int("".join(char for char in part if char.isdigit()) or 0) for part in value.split("."))

        if minimum and maximum and version_tuple(minimum) > version_tuple(maximum):
            raise serializers.ValidationError({"maximum_app_version": "Maximum version must not be below the minimum version."})
        return attrs


class FeedbackPromptEventSerializer(serializers.Serializer):
    policy_id = serializers.PrimaryKeyRelatedField(
        queryset=FeedbackPromptPolicy.objects.filter(is_active=True, is_deleted=False),
        source="policy",
    )
    event = serializers.ChoiceField(choices=["shown", "dismissed"])
    app_version = serializers.CharField(required=False, allow_blank=True, max_length=32)

    def create(self, validated_data):
        return FeedbackPromptEvent.objects.create(user=self.context["request"].user, **validated_data)


class FeedbackPrivacyRequestSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["hide", "delete"])


class AppFeedbackSerializer(serializers.ModelSerializer):
    """User-safe feedback contract. Internal workflow notes and device metadata never leak."""

    votes_count = serializers.IntegerField(source="votes.count", read_only=True)
    has_voted = serializers.SerializerMethodField()
    metadata = serializers.JSONField(write_only=True, required=False, default=dict)
    metric_type = serializers.ChoiceField(choices=FeedbackMetricType.choices, required=False)
    metric_value = serializers.IntegerField(required=False, allow_null=True)

    class Meta:
        model = AppFeedback
        fields = [
            "id",
            "kind",
            "metric_type",
            "metric_value",
            "context",
            "action_key",
            "object_type",
            "object_id",
            "rating",
            "title",
            "comment",
            "suggestion",
            "status",
            "resolution_message",
            "resolved_at",
            "app_version",
            "build_number",
            "platform",
            "locale",
            "release_channel",
            "journey_id",
            "session_id",
            "experiment_key",
            "source_screen",
            "device_model",
            "metadata",
            "votes_count",
            "has_voted",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "resolution_message",
            "resolved_at",
            "votes_count",
            "has_voted",
            "created_at",
            "updated_at",
        ]

    def get_has_voted(self, obj) -> bool:
        request = self.context.get("request")
        return bool(request and request.user.is_authenticated and obj.votes.filter(user=request.user).exists())

    def validate_metadata(self, value):
        return validate_metadata(value)

    def validate(self, attrs):
        kind = attrs.get("kind", getattr(self.instance, "kind", None))
        metric_type = attrs.get("metric_type") or (
            FeedbackMetricType.STARS if kind == FeedbackKind.RATING or attrs.get("rating") is not None else FeedbackMetricType.FREE_TEXT
        )
        attrs["metric_type"] = metric_type
        metric_value = attrs.get("metric_value")
        if metric_value is None and metric_type == FeedbackMetricType.STARS:
            metric_value = attrs.get("rating", getattr(self.instance, "rating", None))
        metric_ranges = {
            FeedbackMetricType.CSAT: (1, 5),
            FeedbackMetricType.CES: (1, 7),
            FeedbackMetricType.NPS: (0, 10),
            FeedbackMetricType.STARS: (1, 5),
        }
        if metric_type in metric_ranges:
            minimum, maximum = metric_ranges[metric_type]
            if metric_value is None or not minimum <= metric_value <= maximum:
                raise serializers.ValidationError({"metric_value": f"{metric_type} must be between {minimum} and {maximum}."})
            attrs["metric_value"] = metric_value
            if metric_type == FeedbackMetricType.STARS:
                attrs["rating"] = metric_value
        elif metric_type == FeedbackMetricType.FREE_TEXT and metric_value is not None:
            raise serializers.ValidationError({"metric_value": "free_text feedback cannot include a numeric value."})
        if kind == FeedbackKind.RATING and metric_type == FeedbackMetricType.FREE_TEXT:
            raise serializers.ValidationError({"metric_type": "A rating cannot use the free_text metric."})
        if kind == FeedbackKind.SUGGESTION and not str(attrs.get("suggestion", "")).strip():
            raise serializers.ValidationError({"suggestion": "Suggestion details are required."})
        if kind == FeedbackKind.SUGGESTION and not str(attrs.get("title", "")).strip():
            raise serializers.ValidationError({"title": "A short suggestion title is required."})
        return attrs

    def create(self, validated_data):
        return FeedbackService.submit(
            self.context["request"].user,
            validated_data,
            request=self.context["request"],
        )


class PublicSuggestionSerializer(serializers.ModelSerializer):
    votes_count = serializers.IntegerField(source="votes.count", read_only=True)
    has_voted = serializers.SerializerMethodField()

    class Meta:
        model = AppFeedback
        fields = [
            "id",
            "context",
            "title",
            "suggestion",
            "status",
            "resolution_message",
            "votes_count",
            "has_voted",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_has_voted(self, obj) -> bool:
        request = self.context.get("request")
        return bool(request and request.user.is_authenticated and obj.votes.filter(user=request.user).exists())


class DashboardFeedbackSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.full_name", read_only=True)
    assigned_to_name = serializers.CharField(source="assigned_to.full_name", read_only=True)
    votes_count = serializers.IntegerField(source="votes.count", read_only=True)

    class Meta:
        model = AppFeedback
        fields = [
            "id",
            "user",
            "user_name",
            "kind",
            "metric_type",
            "metric_value",
            "context",
            "action_key",
            "object_type",
            "object_id",
            "rating",
            "title",
            "comment",
            "suggestion",
            "status",
            "priority",
            "assigned_to",
            "assigned_to_name",
            "internal_notes",
            "resolution_message",
            "resolved_at",
            "app_version",
            "build_number",
            "platform",
            "locale",
            "release_channel",
            "journey_id",
            "session_id",
            "experiment_key",
            "source_screen",
            "device_model",
            "metadata",
            "content_fingerprint",
            "abuse_flags",
            "is_hidden",
            "deletion_requested_at",
            "votes_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class FeedbackWorkflowSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=FeedbackStatus.choices)
    priority = serializers.ChoiceField(choices=FeedbackPriority.choices, required=False)
    assigned_to = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(
            role__in=[UserRole.ADMIN, UserRole.IT_SUPPORT, UserRole.SUPPORT_STAFF],
            is_active=True,
            is_deleted=False,
        ),
        required=False,
        allow_null=True,
    )
    internal_notes = serializers.CharField(required=False, allow_blank=True, max_length=5000)
    resolution_message = serializers.CharField(required=False, allow_blank=True, max_length=5000)

    def validate(self, attrs):
        status_value = attrs["status"]
        if status_value in {FeedbackStatus.RESOLVED, FeedbackStatus.REJECTED, FeedbackStatus.DUPLICATE}:
            current = getattr(self.context.get("feedback"), "resolution_message", "")
            if not str(attrs.get("resolution_message", current)).strip():
                raise serializers.ValidationError(
                    {"resolution_message": "A user-facing resolution message is required for terminal states."}
                )
        return attrs
