from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone
from rest_framework import serializers

from .categories import PREFERENCE_CHANNELS
from .models import DeviceToken, Notification, NotificationPreference, WebPushSubscription
from .webpush_security import (
    AUTH_RAW_LENGTH,
    ENDPOINT_MAX_LENGTH,
    P256DH_RAW_LENGTH,
    InvalidWebPushInput,
    validate_endpoint,
    validate_key,
)


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
            "category",
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


class QuietHoursSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    start = serializers.TimeField(format="%H:%M", input_formats=["%H:%M", "%H:%M:%S"], allow_null=True, required=False)
    end = serializers.TimeField(format="%H:%M", input_formats=["%H:%M", "%H:%M:%S"], allow_null=True, required=False)
    timezone = serializers.CharField(max_length=64, required=False)

    def validate_timezone(self, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise serializers.ValidationError("Unknown time zone.") from exc
        return value


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    """``channels`` switches a push channel off ({"chat": false}); ``quiet_hours`` holds pushes back at night.

    A channel that is not listed is on. The in-app notification is kept in both cases.
    """

    channels = serializers.DictField(child=serializers.BooleanField(), required=False)
    quiet_hours = QuietHoursSerializer(required=False)

    class Meta:
        model = NotificationPreference
        fields = ["in_app_enabled", "push_enabled", "disabled_types", "channels", "quiet_hours", "updated_at"]
        read_only_fields = ["updated_at"]

    def validate_channels(self, value):
        unknown = sorted(set(value) - set(PREFERENCE_CHANNELS))
        if unknown:
            raise serializers.ValidationError(f"Unknown channel(s): {', '.join(unknown)}.")
        return value

    def validate_quiet_hours(self, value):
        if value.get("enabled") and not (value.get("start") and value.get("end")):
            raise serializers.ValidationError("Quiet hours need a start and an end time.")
        return value

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["channels"] = {name: bool(instance.channels.get(name, True)) for name in PREFERENCE_CHANNELS}
        data["quiet_hours"] = {
            "enabled": instance.quiet_enabled,
            "start": instance.quiet_start.strftime("%H:%M") if instance.quiet_start else None,
            "end": instance.quiet_end.strftime("%H:%M") if instance.quiet_end else None,
            "timezone": instance.quiet_timezone,
        }
        return data

    def update(self, instance, validated_data):
        channels = validated_data.pop("channels", None)
        quiet = validated_data.pop("quiet_hours", None)
        if channels is not None:
            instance.channels = {**instance.channels, **channels}
        if quiet is not None:
            instance.quiet_enabled = quiet["enabled"]
            if "start" in quiet:
                instance.quiet_start = quiet["start"]
            if "end" in quiet:
                instance.quiet_end = quiet["end"]
            if "timezone" in quiet:
                instance.quiet_timezone = quiet["timezone"]
        return super().update(instance, validated_data)

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


def _checked(validator, *args, **kwargs):
    try:
        return validator(*args, **kwargs)
    except InvalidWebPushInput as exc:
        raise serializers.ValidationError(str(exc)) from exc


class WebPushKeysSerializer(serializers.Serializer):
    p256dh = serializers.CharField(max_length=255)
    auth = serializers.CharField(max_length=255)

    def validate_p256dh(self, value: str) -> str:
        return _checked(validate_key, value, raw_length=P256DH_RAW_LENGTH, field="p256dh")

    def validate_auth(self, value: str) -> str:
        return _checked(validate_key, value, raw_length=AUTH_RAW_LENGTH, field="auth")


class WebPushSubscriptionSerializer(serializers.ModelSerializer):
    """Body of ``PushSubscription.toJSON()`` (``endpoint`` + ``keys``) plus an optional ``user_agent``.

    The endpoint and keys are write-only: the endpoint is a capability URL and is never echoed back.
    """

    endpoint = serializers.CharField(max_length=ENDPOINT_MAX_LENGTH, write_only=True, validators=[])
    keys = WebPushKeysSerializer(write_only=True)
    user_agent = serializers.CharField(max_length=255, required=False, allow_blank=True)

    class Meta:
        model = WebPushSubscription
        fields = ["id", "endpoint", "keys", "user_agent", "is_active", "last_used_at", "created_at", "updated_at"]
        read_only_fields = ["id", "is_active", "last_used_at", "created_at", "updated_at"]

    def validate_endpoint(self, value: str) -> str:
        return _checked(validate_endpoint, value)

    def create(self, validated_data):
        request = self.context["request"]
        keys = validated_data["keys"]
        user_agent = validated_data.get("user_agent") or request.headers.get("User-Agent", "")
        subscription, _ = WebPushSubscription.objects.update_or_create(
            endpoint=validated_data["endpoint"],
            defaults={
                "user": request.user,
                "p256dh": keys["p256dh"],
                "auth": keys["auth"],
                "user_agent": user_agent[:255],
                "is_active": True,
                "failure_count": 0,
                "last_used_at": timezone.now(),
                "is_deleted": False,
                "deleted_at": None,
            },
        )
        return subscription


class WebPushUnsubscribeSerializer(serializers.Serializer):
    endpoint = serializers.CharField(max_length=ENDPOINT_MAX_LENGTH)


class WebPushPublicKeySerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    public_key = serializers.CharField(allow_null=True)
