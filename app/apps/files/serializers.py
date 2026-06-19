from rest_framework import serializers
from drf_spectacular.utils import extend_schema_field

from apps.common.upload_validation import validate_document_upload
from apps.universities.models import validate_academic_hierarchy

from .models import FileResource, FileVisibility


class FileResourceSerializer(serializers.ModelSerializer):
    file = serializers.FileField(validators=[validate_document_upload])
    uploaded_by_name = serializers.CharField(source="uploaded_by.full_name", read_only=True)
    secure_file_url = serializers.SerializerMethodField()

    class Meta:
        model = FileResource
        fields = [
            "id",
            "title",
            "description",
            "file",
            "secure_file_url",
            "file_type",
            "file_size",
            "uploaded_by",
            "uploaded_by_name",
            "university",
            "faculty",
            "major",
            "academic_year",
            "semester",
            "subject",
            "group",
            "visibility",
            "is_printable",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "file_type", "file_size", "uploaded_by", "created_at", "updated_at"]

    @extend_schema_field(serializers.CharField())
    def get_secure_file_url(self, obj):
        request = self.context.get("request")
        path = f"/api/v1/files/{obj.id}/view/"
        return request.build_absolute_uri(path) if request else path

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance.visibility != FileVisibility.PUBLIC:
            data["file"] = None
        return data

    def validate(self, attrs):
        instance = self.instance
        visibility = attrs.get("visibility") or getattr(instance, "visibility", FileVisibility.PUBLIC)
        group = attrs.get("group") or getattr(instance, "group", None)
        major = attrs.get("major") or getattr(instance, "major", None)
        academic_year = attrs.get("academic_year") or getattr(instance, "academic_year", None)
        try:
            validate_academic_hierarchy(
                university=attrs.get("university") or getattr(instance, "university", None),
                faculty=attrs.get("faculty") or getattr(instance, "faculty", None),
                major=major,
                academic_year=academic_year,
                semester=attrs.get("semester") or getattr(instance, "semester", None),
                subject=attrs.get("subject") or getattr(instance, "subject", None),
            )
        except Exception as exc:
            raise serializers.ValidationError(getattr(exc, "message_dict", str(exc))) from exc
        if visibility == FileVisibility.GROUP_ONLY and not group:
            raise serializers.ValidationError({"group": "Group is required for group-only files."})
        if visibility == FileVisibility.MAJOR_ONLY and (not major or not academic_year):
            raise serializers.ValidationError({"major": "Major and academic year are required for major-only files."})
        return attrs
