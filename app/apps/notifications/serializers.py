from rest_framework import serializers
from django.db import IntegrityError, transaction

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
        defaults = {
            "user": self.context["request"].user,
            "platform": validated_data["platform"],
            "is_active": True,
        }
        try:
            with transaction.atomic():
                device, _ = DeviceToken.objects.update_or_create(token=token, defaults=defaults)
        except IntegrityError:
            device = DeviceToken.objects.get(token=token)
            for field, value in defaults.items():
                setattr(device, field, value)
            device.save(update_fields=["user", "platform", "is_active", "updated_at"])
        return device
