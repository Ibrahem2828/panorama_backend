from __future__ import annotations

from django.utils import timezone
from rest_framework import serializers

from .models import DeviceToken, Notification, NotificationPreference


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = [
            "id",
            "title",
            "body",
            "title_ar",
            "title_en",
            "body_ar",
            "body_en",
            "type",
            "data",
            "deep_link",
            "related_object_type",
            "related_object_id",
            "expires_at",
            "is_read",
            "read_at",
            "created_at",
        ]
        read_only_fields = fields


class DeviceTokenSerializer(serializers.ModelSerializer):
    token = serializers.CharField(max_length=512, validators=[], write_only=True)
    token_hint = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = DeviceToken
        fields = ["id", "token", "token_hint", "platform", "is_active", "last_used_at", "created_at", "updated_at"]
        read_only_fields = ["id", "token_hint", "is_active", "last_used_at", "created_at", "updated_at"]

    def get_token_hint(self, obj) -> str:
        token = str(obj.token or "")
        return f"***{token[-8:]}" if token else ""

    def validate_token(self, value):
        value = str(value or "").strip()
        if len(value) < 20:
            raise serializers.ValidationError("The device push token is invalid.")
        return value

    def create(self, validated_data):
        token = validated_data["token"]
        device, _ = DeviceToken.objects.update_or_create(
            token=token,
            defaults={
                "user": self.context["request"].user,
                "platform": validated_data["platform"],
                "is_active": True,
                "last_used_at": timezone.now(),
                "is_deleted": False,
                "deleted_at": None,
            },
        )
        return device


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = ["in_app_enabled", "push_enabled", "disabled_types", "updated_at"]
        read_only_fields = ["updated_at"]

    def validate_disabled_types(self, value):
        if not isinstance(value, list) or not all(isinstance(item, str) and len(item) <= 32 for item in value):
            raise serializers.ValidationError("disabled_types must be a list of notification type names.")
        return sorted(set(value))


class NotificationCampaignSerializer(serializers.Serializer):
    user_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), min_length=1, max_length=1000)
    title = serializers.CharField(max_length=255)
    body = serializers.CharField(max_length=2000)
    type = serializers.ChoiceField(choices=list(Notification._meta.get_field("type").choices or ()), default="system")
    deep_link = serializers.CharField(max_length=255, required=False, allow_blank=True)
    deduplication_key = serializers.CharField(max_length=128, required=False, allow_blank=True)

    def validate_deep_link(self, value: str) -> str:
        if value and not value.startswith("/"):
            raise serializers.ValidationError("deep_link must be a relative application path.")
        return value
