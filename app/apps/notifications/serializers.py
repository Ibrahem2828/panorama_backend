from rest_framework import serializers

from .models import DeviceToken, Notification


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "title", "body", "type", "data", "is_read", "read_at", "created_at"]
        read_only_fields = fields


class DeviceTokenSerializer(serializers.ModelSerializer):
    token = serializers.CharField(max_length=512, validators=[])

    class Meta:
        model = DeviceToken
        fields = ["id", "token", "platform", "is_active", "last_used_at", "created_at", "updated_at"]
        read_only_fields = ["id", "is_active", "last_used_at", "created_at", "updated_at"]

    def create(self, validated_data):
        token = validated_data["token"]
        device, _ = DeviceToken.objects.update_or_create(
            token=token,
            defaults={
                "user": self.context["request"].user,
                "platform": validated_data["platform"],
                "is_active": True,
            },
        )
        return device
