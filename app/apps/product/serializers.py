from __future__ import annotations

from rest_framework import serializers

from .models import (
    AccountDeletionRequest,
    DeviceInstallation,
    FeatureFlag,
    MaintenanceMode,
    MobileAppReleasePolicy,
    PrivacyPolicyVersion,
    TermsVersion,
    UserConsent,
)


class MobileAppReleasePolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = MobileAppReleasePolicy
        fields = [
            "id",
            "platform",
            "minimum_supported_version",
            "minimum_supported_build",
            "latest_version",
            "latest_build",
            "update_mode",
            "store_url",
            "message_ar",
            "message_en",
            "grace_period_ends_at",
            "is_active",
            "emergency_update_bypass",
            "updated_at",
        ]
        read_only_fields = ["id", "updated_at"]

    def validate(self, attrs):
        minimum = attrs.get("minimum_supported_build", getattr(self.instance, "minimum_supported_build", 0))
        latest = attrs.get("latest_build", getattr(self.instance, "latest_build", 0))
        if minimum > latest:
            raise serializers.ValidationError({"minimum_supported_build": "Cannot exceed latest_build."})
        return attrs


class MaintenanceModeSerializer(serializers.ModelSerializer):
    is_currently_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = MaintenanceMode
        fields = [
            "id",
            "enabled",
            "title_ar",
            "title_en",
            "message_ar",
            "message_en",
            "starts_at",
            "ends_at",
            "estimated_end_at",
            "allowed_roles",
            "bypass_staff",
            "bypass_healthchecks",
            "retry_after_seconds",
            "is_currently_active",
            "updated_at",
        ]
        read_only_fields = ["id", "updated_at", "is_currently_active"]

    def validate(self, attrs):
        starts_at = attrs.get("starts_at", getattr(self.instance, "starts_at", None))
        ends_at = attrs.get("ends_at", getattr(self.instance, "ends_at", None))
        if starts_at and ends_at and ends_at <= starts_at:
            raise serializers.ValidationError({"ends_at": "Must be after starts_at."})
        return attrs


class FeatureFlagSerializer(serializers.ModelSerializer):
    class Meta:
        model = FeatureFlag
        fields = ["id", "key", "enabled", "platform", "role", "expose_to_mobile", "description", "updated_at"]
        read_only_fields = ["id", "updated_at"]


class DeviceInstallationSerializer(serializers.ModelSerializer):
    push_token = serializers.CharField(
        max_length=512, required=False, allow_blank=True, allow_null=True, write_only=True
    )
    push_token_hint = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = DeviceInstallation
        fields = [
            "installation_id",
            "platform",
            "app_version",
            "build_number",
            "push_token",
            "push_token_hint",
            "locale",
            "timezone_name",
            "device_model",
            "last_seen_at",
            "notifications_enabled",
            "revoked_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["last_seen_at", "revoked_at", "created_at", "updated_at", "push_token_hint"]

    def validate_push_token(self, value: str | None) -> str | None:
        value = str(value or "").strip() or None
        if value and len(value) < 20:
            raise serializers.ValidationError("The push token is invalid.")
        return value

    def validate_device_model(self, value: str) -> str:
        return value.strip()[:80]

    def get_push_token_hint(self, obj: DeviceInstallation) -> str:
        return f"***{obj.push_token[-8:]}" if obj.push_token else ""


class PublicPolicyVersionSerializer(serializers.ModelSerializer):
    class Meta:
        fields = ["version", "content_url", "requires_reacceptance", "published_at"]


class TermsVersionSerializer(PublicPolicyVersionSerializer):
    class Meta(PublicPolicyVersionSerializer.Meta):
        model = TermsVersion
        fields = ["id", "version", "content_url", "is_active", "requires_reacceptance", "published_at", "updated_at"]
        read_only_fields = ["id", "updated_at"]


class PrivacyPolicyVersionSerializer(PublicPolicyVersionSerializer):
    class Meta(PublicPolicyVersionSerializer.Meta):
        model = PrivacyPolicyVersion
        fields = ["id", "version", "content_url", "is_active", "requires_reacceptance", "published_at", "updated_at"]
        read_only_fields = ["id", "updated_at"]


class PolicyAcceptanceSerializer(serializers.Serializer):
    terms_version = serializers.CharField(max_length=64, required=False)
    privacy_version = serializers.CharField(max_length=64, required=False)
    locale = serializers.ChoiceField(choices=["ar", "en"], required=False, default="ar")

    def validate(self, attrs):
        if not attrs.get("terms_version") and not attrs.get("privacy_version"):
            raise serializers.ValidationError("Provide terms_version and/or privacy_version.")
        return attrs


class UserConsentSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserConsent
        fields = ["kind", "version", "locale", "accepted_at"]
        read_only_fields = fields


class AccountDeletionRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = AccountDeletionRequest
        fields = ["status", "requested_at", "scheduled_for", "cancelled_at", "completed_at", "reason"]
        read_only_fields = ["status", "requested_at", "scheduled_for", "cancelled_at", "completed_at"]

    def validate_reason(self, value: str) -> str:
        return value.strip()[:500]
