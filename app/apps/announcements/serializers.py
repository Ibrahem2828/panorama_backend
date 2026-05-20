from rest_framework import serializers

from apps.universities.models import validate_academic_hierarchy

from .models import Announcement


class AnnouncementSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source="created_by.full_name", read_only=True)

    class Meta:
        model = Announcement
        fields = [
            "id",
            "title",
            "description",
            "image",
            "link",
            "target_user_type",
            "target_university",
            "target_faculty",
            "target_major",
            "target_academic_year",
            "target_semester",
            "starts_at",
            "ends_at",
            "is_active",
            "created_by",
            "created_by_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_by", "created_at", "updated_at"]

    def validate(self, attrs):
        instance = self.instance
        try:
            validate_academic_hierarchy(
                university=attrs.get("target_university") or getattr(instance, "target_university", None),
                faculty=attrs.get("target_faculty") or getattr(instance, "target_faculty", None),
                major=attrs.get("target_major") or getattr(instance, "target_major", None),
                academic_year=attrs.get("target_academic_year") or getattr(instance, "target_academic_year", None),
                semester=attrs.get("target_semester") or getattr(instance, "target_semester", None),
            )
        except Exception as exc:
            raise serializers.ValidationError(getattr(exc, "message_dict", str(exc))) from exc
        starts_at = attrs.get("starts_at") or getattr(instance, "starts_at", None)
        ends_at = attrs.get("ends_at") or getattr(instance, "ends_at", None)
        if starts_at and ends_at and ends_at < starts_at:
            raise serializers.ValidationError({"ends_at": "End date must be after start date."})
        return attrs
