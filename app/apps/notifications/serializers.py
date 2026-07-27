from __future__ import annotations

from django.utils import timezone
from rest_framework import serializers

from .models import DeviceToken, Notification


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "title", "body", "type", "data", "is_read", "read_at", "created_at"]
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
