from __future__ import annotations

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .choices import PermissionEffect, UserRole
from .models import User, UserPermissionOverride
from .permissions import Capability, PermissionService

ALL_CAPABILITIES = sorted(
    value for name, value in vars(Capability).items() if name.isupper() and isinstance(value, str)
)


class DashboardUserSerializer(serializers.ModelSerializer):
    effective_capabilities = serializers.SerializerMethodField()
    overrides = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "full_name",
            "username",
            "email",
            "phone_number",
            "role",
            "is_active",
            "is_staff",
            "is_phone_verified",
            "is_email_verified",
            "date_joined",
            "last_login",
            "effective_capabilities",
            "overrides",
        ]
        read_only_fields = [
            "id",
            "username",
            "email",
            "phone_number",
            "is_active",
            "is_staff",
            "is_phone_verified",
            "is_email_verified",
            "date_joined",
            "last_login",
            "effective_capabilities",
            "overrides",
        ]

    def get_effective_capabilities(self, obj) -> list[str]:
        return [code for code in ALL_CAPABILITIES if PermissionService.has(obj, code)]

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_overrides(self, obj) -> list[dict[str, object]]:
        return list(
            UserPermissionOverrideSerializer(
                obj.permission_overrides.filter(is_deleted=False).order_by("permission_code"),
                many=True,
            ).data
        )


class DashboardUserUpdateSerializer(serializers.ModelSerializer):
    """The narrow PATCH surface for dashboard user administration."""

    class Meta:
        model = User
        fields = ["full_name", "role"]

    def validate(self, attrs):
        unexpected = set(self.initial_data) - set(self.fields)
        if unexpected:
            raise serializers.ValidationError(
                {field: "This field is not writable through dashboard user PATCH." for field in sorted(unexpected)}
            )
        instance = self.instance
        request = self.context["request"]
        if instance and instance.pk == request.user.pk and attrs.get("role", instance.role) != instance.role:
            raise serializers.ValidationError({"role": "You cannot change your own role."})
        if (
            instance
            and instance.role == UserRole.IT_SUPPORT
            and attrs.get("role", instance.role) != UserRole.IT_SUPPORT
            and not User.objects.filter(role=UserRole.IT_SUPPORT, is_active=True, is_deleted=False)
            .exclude(pk=instance.pk)
            .exists()
        ):
            raise serializers.ValidationError({"role": "The system must keep at least one active IT Support account."})
        return attrs

    def validate_role(self, value):
        request = self.context["request"]
        if value == UserRole.IT_SUPPORT and request.user.role != UserRole.IT_SUPPORT:
            raise serializers.ValidationError("Only IT Support can grant the IT Support role.")
        return value


class DashboardDeactivateUserSerializer(serializers.Serializer):
    reason = serializers.CharField(min_length=3, max_length=255, trim_whitespace=True)


class UserPermissionOverrideSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserPermissionOverride
        fields = ["id", "permission_code", "effect", "expires_at", "reason", "granted_by", "created_at", "updated_at"]
        read_only_fields = ["id", "granted_by", "created_at", "updated_at"]

    def validate_permission_code(self, value):
        if value not in ALL_CAPABILITIES:
            raise serializers.ValidationError("Unknown capability code.")
        return value

    def validate_effect(self, value):
        if value not in PermissionEffect.values:
            raise serializers.ValidationError("Invalid permission effect.")
        return value


class PermissionOverrideUpsertSerializer(UserPermissionOverrideSerializer):
    class Meta(UserPermissionOverrideSerializer.Meta):
        read_only_fields = ["id", "granted_by", "created_at", "updated_at"]
