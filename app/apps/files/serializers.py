from __future__ import annotations

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.common.file_validation import validate_library_upload
from apps.universities.models import validate_academic_hierarchy

from .document_inspection import compute_sha256, detect_pages_count
from .models import FileProcessingStatus, FileResource, FileVisibility


class FileResourceSerializer(serializers.ModelSerializer):
    uploaded_by_name = serializers.CharField(source="uploaded_by.full_name", read_only=True)
    file = serializers.FileField(write_only=True)
    preview_ticket_endpoint = serializers.SerializerMethodField()
    download_allowed = serializers.SerializerMethodField()
    viewer_ready = serializers.SerializerMethodField()
    viewer_endpoint = serializers.SerializerMethodField()

    class Meta:
        model = FileResource
        fields = [
            "id",
            "title",
            "description",
            "file",
            "file_type",
            "file_size",
            "pages_count",
            "sha256",
            "preview_ticket_endpoint",
            "download_allowed",
            "processing_status",
            "failure_code",
            "failure_message",
            "processed_at",
            "viewer_ready",
            "viewer_endpoint",
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
        read_only_fields = [
            "id",
            "file_type",
            "file_size",
            "pages_count",
            "sha256",
            "preview_ticket_endpoint",
            "download_allowed",
            "processing_status",
            "failure_code",
            "failure_message",
            "processed_at",
            "viewer_ready",
            "viewer_endpoint",
            "uploaded_by",
            "created_at",
            "updated_at",
        ]

    @extend_schema_field(serializers.CharField())
    def get_preview_ticket_endpoint(self, obj):
        return f"/api/v1/files/{obj.pk}/access-ticket/"

    @extend_schema_field(serializers.BooleanField())
    def get_download_allowed(self, obj):
        return False

    @extend_schema_field(serializers.BooleanField())
    def get_viewer_ready(self, obj):
        return obj.viewer_ready

    @extend_schema_field(serializers.CharField())
    def get_viewer_endpoint(self, obj):
        return f"/api/v1/files/{obj.pk}/viewer/"

    def validate_file(self, value):
        return validate_library_upload(value, "file")

    @staticmethod
    def _prepare_upload(validated_data: dict, uploaded) -> None:
        pages = detect_pages_count(uploaded, uploaded.name)
        validated_data["pages_count"] = pages or None
        validated_data["sha256"] = compute_sha256(uploaded)
        validated_data["processing_status"] = FileProcessingStatus.QUEUED
        validated_data["failure_code"] = ""
        validated_data["failure_message"] = ""
        if not pages:
            # Office documents cannot be sent through the print pipeline as they are; they are read in the viewer only.
            validated_data["is_printable"] = False

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

    def create(self, validated_data):
        uploaded = validated_data["file"]
        self._prepare_upload(validated_data, uploaded)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        uploaded = validated_data.get("file")
        if uploaded:
            self._prepare_upload(validated_data, uploaded)
        return super().update(instance, validated_data)
