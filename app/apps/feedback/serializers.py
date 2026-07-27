from __future__ import annotations

from rest_framework import serializers

from apps.accounts.choices import UserRole
from apps.accounts.models import User

from .models import (
    AppFeedback,
    FeedbackKind,
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
            "cooldown_days",
            "sample_percent",
            "allow_comment",
            "allow_suggestion",
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
            "cooldown_days",
            "sample_percent",
            "allow_comment",
            "allow_suggestion",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class FeedbackPromptEventSerializer(serializers.Serializer):
    policy_id = serializers.PrimaryKeyRelatedField(
        queryset=FeedbackPromptPolicy.objects.filter(is_active=True, is_deleted=False),
        source="policy",
    )
    event = serializers.ChoiceField(choices=["shown", "dismissed"])
    app_version = serializers.CharField(required=False, allow_blank=True, max_length=32)

    def create(self, validated_data):
        return FeedbackPromptEvent.objects.create(user=self.context["request"].user, **validated_data)


class AppFeedbackSerializer(serializers.ModelSerializer):
    """User-safe feedback contract. Internal workflow notes and device metadata never leak."""

    votes_count = serializers.IntegerField(source="votes.count", read_only=True)
    has_voted = serializers.SerializerMethodField()
    metadata = serializers.JSONField(write_only=True, required=False, default=dict)

    class Meta:
        model = AppFeedback
        fields = [
            "id",
            "kind",
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
        if kind == FeedbackKind.RATING and attrs.get("rating") is None:
            raise serializers.ValidationError({"rating": "A rating from 1 to 5 is required."})
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
            "device_model",
            "metadata",
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
