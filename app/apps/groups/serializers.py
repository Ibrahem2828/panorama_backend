from __future__ import annotations

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.common.file_validation import validate_image_upload
from apps.universities.models import validate_academic_hierarchy

from .models import (
    ExternalChannelType,
    Group,
    GroupExternalChannel,
    GroupMembership,
    GroupMembershipRole,
    GroupMembershipStatus,
)
from .services import ExternalChannelService


class GroupSerializer(serializers.ModelSerializer):
    membership_status = serializers.SerializerMethodField()
    current_user_membership_status = serializers.SerializerMethodField()
    current_user_group_role = serializers.SerializerMethodField()
    members_count = serializers.SerializerMethodField()
    has_whatsapp_channel = serializers.SerializerMethodField()

    class Meta:
        model = Group
        fields = [
            "id",
            "name",
            "description",
            "image",
            "university",
            "faculty",
            "major",
            "academic_year",
            "semester",
            "subject",
            "created_by",
            "is_active",
            "requires_approval",
            "send_messages_permission",
            "membership_status",
            "current_user_membership_status",
            "current_user_group_role",
            "members_count",
            "has_whatsapp_channel",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "created_by",
            "membership_status",
            "current_user_membership_status",
            "current_user_group_role",
            "members_count",
            "has_whatsapp_channel",
            "created_at",
            "updated_at",
        ]

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_membership_status(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None
        membership = getattr(obj, "_current_membership", None)
        if membership is None:
            membership = obj.memberships.filter(user=request.user, is_deleted=False).first()
        return membership.status if membership else None

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_current_user_membership_status(self, obj):
        return self.get_membership_status(obj)

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_current_user_group_role(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None
        membership = (
            getattr(obj, "_current_membership", None)
            or obj.memberships.filter(
                user=request.user,
                is_deleted=False,
            ).first()
        )
        return membership.role if membership else None

    @extend_schema_field(serializers.IntegerField())
    def get_members_count(self, obj):
        value = getattr(obj, "members_count", None)
        if value is not None:
            return value
        return obj.memberships.filter(status=GroupMembershipStatus.APPROVED, is_deleted=False).count()

    @extend_schema_field(serializers.BooleanField())
    def get_has_whatsapp_channel(self, obj):
        prefetched = getattr(obj, "active_external_channels", None)
        if prefetched is not None:
            return any(channel.channel_type == ExternalChannelType.WHATSAPP for channel in prefetched)
        return obj.external_channels.filter(
            channel_type=ExternalChannelType.WHATSAPP,
            is_active=True,
            is_deleted=False,
        ).exists()

    def validate_image(self, value):
        return validate_image_upload(value, "image") if value else value

    def validate(self, attrs):
        instance = self.instance
        try:
            validate_academic_hierarchy(
                university=attrs.get("university") or getattr(instance, "university", None),
                faculty=attrs.get("faculty") or getattr(instance, "faculty", None),
                major=attrs.get("major") or getattr(instance, "major", None),
                academic_year=attrs.get("academic_year") or getattr(instance, "academic_year", None),
                semester=attrs.get("semester") or getattr(instance, "semester", None),
                subject=attrs.get("subject") or getattr(instance, "subject", None),
            )
        except Exception as exc:
            raise serializers.ValidationError(getattr(exc, "message_dict", str(exc))) from exc
        return attrs


class DashboardGroupSerializer(GroupSerializer):
    whatsapp_url = serializers.URLField(write_only=True, required=False, allow_blank=True)
    whatsapp_enabled = serializers.BooleanField(write_only=True, required=False, default=True)
    whatsapp_label = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=100)

    class Meta(GroupSerializer.Meta):
        fields = GroupSerializer.Meta.fields + ["whatsapp_url", "whatsapp_enabled", "whatsapp_label"]

    def create(self, validated_data):
        whatsapp_url = validated_data.pop("whatsapp_url", "")
        enabled = validated_data.pop("whatsapp_enabled", True)
        label = validated_data.pop("whatsapp_label", "")
        group = super().create(validated_data)
        if whatsapp_url:
            ExternalChannelService.set_whatsapp_channel(
                group,
                whatsapp_url,
                actor=self.context["request"].user,
                is_active=enabled,
                label=label,
            )
        return group

    def update(self, instance, validated_data):
        sentinel = object()
        whatsapp_url = validated_data.pop("whatsapp_url", sentinel)
        enabled = validated_data.pop("whatsapp_enabled", True)
        label = validated_data.pop("whatsapp_label", "")
        group = super().update(instance, validated_data)
        if whatsapp_url is not sentinel:
            if whatsapp_url:
                ExternalChannelService.set_whatsapp_channel(
                    group,
                    whatsapp_url,
                    actor=self.context["request"].user,
                    is_active=enabled,
                    label=label,
                )
            else:
                GroupExternalChannel.objects.filter(
                    group=group,
                    channel_type=ExternalChannelType.WHATSAPP,
                ).update(is_active=False)
        return group


class WhatsAppChannelUpdateSerializer(serializers.Serializer):
    url = serializers.URLField()
    is_active = serializers.BooleanField(default=True)
    label = serializers.CharField(required=False, allow_blank=True, max_length=100)

    def validate_url(self, value):
        return ExternalChannelService.validate_whatsapp_url(value)


class ExternalChannelTicketSerializer(serializers.Serializer):
    open_url = serializers.URLField(read_only=True)
    expires_at = serializers.DateTimeField(read_only=True)


class GroupMembershipSerializer(serializers.ModelSerializer):
    group_name = serializers.CharField(source="group.name", read_only=True)
    user_name = serializers.CharField(source="user.full_name", read_only=True)

    class Meta:
        model = GroupMembership
        fields = [
            "id",
            "group",
            "group_name",
            "user",
            "user_name",
            "role",
            "status",
            "reviewed_by",
            "reviewed_at",
            "joined_at",
            "created_at",
        ]
        read_only_fields = fields


class GroupMembershipRoleUpdateSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=GroupMembershipRole.choices)
