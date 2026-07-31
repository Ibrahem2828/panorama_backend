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
        return UserPermissionOverrideSerializer(
            obj.permission_overrides.filter(is_deleted=False).order_by("permission_code"), many=True
        ).data

    def validate_role(self, value):
        request = self.context["request"]
        if value == UserRole.IT_SUPPORT and request.user.role != UserRole.IT_SUPPORT:
            raise serializers.ValidationError("Only IT Support can grant the IT Support role.")
        return value

    def update(self, instance, validated_data):
        request = self.context["request"]
        if instance.pk == request.user.pk:
            if "role" in validated_data and validated_data["role"] != instance.role:
                raise serializers.ValidationError({"role": "You cannot change your own role."})
            if validated_data.get("is_active") is False:
                raise serializers.ValidationError({"is_active": "You cannot disable your own account."})
        if instance.role == UserRole.IT_SUPPORT and instance.is_active:
            removing_last = (
                validated_data.get("role", instance.role) != UserRole.IT_SUPPORT
                or validated_data.get("is_active", instance.is_active) is False
            )
            if (
                removing_last
                and not User.objects.filter(role=UserRole.IT_SUPPORT, is_active=True, is_deleted=False)
                .exclude(pk=instance.pk)
                .exists()
            ):
                raise serializers.ValidationError("The system must keep at least one active IT Support account.")
        return super().update(instance, validated_data)


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
