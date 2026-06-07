from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.common.upload_validation import validate_image_upload
from apps.universities.models import validate_academic_hierarchy

from .models import Group, GroupMembership, GroupMembershipRole, GroupMembershipStatus


class GroupSerializer(serializers.ModelSerializer):
    image = serializers.ImageField(required=False, allow_null=True, validators=[validate_image_upload])
    membership_status = serializers.SerializerMethodField()
    current_user_membership_status = serializers.SerializerMethodField()
    current_user_group_role = serializers.SerializerMethodField()
    members_count = serializers.SerializerMethodField()

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
            "created_at",
            "updated_at",
        ]

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_membership_status(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None
        membership = getattr(obj, "_current_membership", None)
        if membership is None and request and request.user.is_authenticated:
            membership = obj.memberships.filter(user=request.user).first()
        return membership.status if membership else None

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_current_user_membership_status(self, obj):
        return self.get_membership_status(obj)

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_current_user_group_role(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None
        membership = getattr(obj, "_current_membership", None) or obj.memberships.filter(user=request.user).first()
        return membership.role if membership else None

    @extend_schema_field(serializers.IntegerField())
    def get_members_count(self, obj):
        value = getattr(obj, "members_count", None)
        if value is not None:
            return value
        return obj.memberships.filter(status=GroupMembershipStatus.APPROVED).count()

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
