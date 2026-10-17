from __future__ import annotations

import hashlib
import json
from pathlib import Path

from django.utils import timezone
from django.utils.html import strip_tags
from rest_framework import serializers

from apps.universities.models import Faculty, Subject

from .document_pipeline import DocumentPipelineError, inspect_lecture_document
from .models import Lecture, LectureNote, LectureProcessingStatus


def _subject_name(lecture: Lecture) -> str | None:
    subject = lecture.subject
    return subject.name if subject else None


def _faculty_name(lecture: Lecture) -> str | None:
    subject = lecture.subject
    faculty = subject.faculty if subject else lecture.faculty
    return faculty.name if faculty else None


class LectureSerializer(serializers.ModelSerializer):
    subject_name = serializers.SerializerMethodField()
    faculty_name = serializers.SerializerMethodField()
    is_general = serializers.BooleanField(read_only=True)
    can_view = serializers.SerializerMethodField()

    class Meta:
        model = Lecture
        fields = [
            "id",
            "subject",
            "subject_name",
            "faculty",
            "faculty_name",
            "is_general",
            "title",
            "description",
            "status",
            "page_count",
            "is_published",
            "published_at",
            "can_view",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_subject_name(self, lecture: Lecture) -> str | None:
        return _subject_name(lecture)

    def get_faculty_name(self, lecture: Lecture) -> str | None:
        return _faculty_name(lecture)

    def get_can_view(self, lecture: Lecture) -> bool:
        return lecture.is_ready_for_students


class DashboardLectureSerializer(serializers.ModelSerializer):
    original_file = serializers.FileField(write_only=True, required=False)
    subject = serializers.PrimaryKeyRelatedField(
        queryset=Subject.objects.filter(is_deleted=False), required=False, allow_null=True
    )
    faculty = serializers.PrimaryKeyRelatedField(
        queryset=Faculty.objects.filter(is_deleted=False), required=False, allow_null=True
    )
    subject_name = serializers.SerializerMethodField()
    faculty_name = serializers.SerializerMethodField()
    is_general = serializers.BooleanField(read_only=True)

    class Meta:
        model = Lecture
        fields = [
            "id",
            "subject",
            "subject_name",
            "faculty",
            "faculty_name",
            "is_general",
            "title",
            "description",
            "original_file",
            "original_filename",
            "original_mime_type",
            "original_size",
            "original_sha256",
            "status",
            "page_count",
            "failure_code",
            "failure_message",
            "is_published",
            "published_at",
            "uploaded_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "original_filename",
            "original_mime_type",
            "original_size",
            "original_sha256",
            "status",
            "page_count",
            "failure_code",
            "failure_message",
            "published_at",
            "uploaded_by",
            "created_at",
            "updated_at",
        ]

    def get_subject_name(self, lecture: Lecture) -> str | None:
        return _subject_name(lecture)

    def get_faculty_name(self, lecture: Lecture) -> str | None:
        return _faculty_name(lecture)

    def validate(self, attrs):
        subject = attrs.get("subject", self.instance.subject if self.instance else None)
        faculty = attrs.get("faculty", self.instance.faculty if self.instance else None)
        if subject and faculty:
            raise serializers.ValidationError(
                {"faculty": "A lecture linked to a subject takes its faculty from the subject; leave it empty."}
            )
        if not self.instance and "original_file" not in attrs:
            raise serializers.ValidationError({"original_file": "An original document is required."})
        if self.instance and "original_file" in attrs:
            raise serializers.ValidationError(
                {"original_file": "Create a new lecture to replace an original document."}
            )
        if "original_file" not in attrs:
            return attrs
        try:
            inspection = inspect_lecture_document(attrs["original_file"])
        except DocumentPipelineError as exc:
            raise serializers.ValidationError({"original_file": str(exc)}) from exc
        duplicates = Lecture.objects.filter(original_sha256=inspection.sha256, is_deleted=False)
        duplicates = duplicates.filter(subject=subject) if subject else duplicates.filter(subject__isnull=True)
        if duplicates.exists():
            raise serializers.ValidationError(
                {"original_file": "This source document is already registered for the subject."}
                if subject
                else {"original_file": "This source document is already registered as a general lecture."}
            )
        attrs["_document_inspection"] = inspection
        return attrs

    def update(self, instance, validated_data):
        # Publishing a lecture that is already converted must stamp the publication time (the conversion task only
        # does it for lectures that were published before they became ready).
        if validated_data.get("is_published") and not instance.is_published and not instance.published_at:
            validated_data["published_at"] = timezone.now()
        return super().update(instance, validated_data)

    def create(self, validated_data):
        inspection = validated_data.pop("_document_inspection")
        uploaded = validated_data["original_file"]
        return Lecture.objects.create(
            **validated_data,
            original_filename=Path(uploaded.name).name,
            original_mime_type=inspection.mime_type,
            original_size=inspection.size,
            original_sha256=inspection.sha256,
            status=LectureProcessingStatus.QUEUED,
        )


class LectureNoteSerializer(serializers.ModelSerializer):
    version = serializers.IntegerField(required=False)

    class Meta:
        model = LectureNote
        fields = [
            "id",
            "page_number",
            "selected_text",
            "anchor_data",
            "content",
            "color",
            "note_type",
            "is_bookmark",
            "is_favorite",
            "version",
            "archived_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "archived_at", "created_at", "updated_at"]

    def validate_content(self, value: str) -> str:
        value = strip_tags(value).strip()
        if not value:
            raise serializers.ValidationError("Note content cannot be empty.")
        if len(value) > 10_000:
            raise serializers.ValidationError("Note content exceeds 10000 characters.")
        return value

    def validate_selected_text(self, value: str) -> str:
        value = strip_tags(value).strip()
        if len(value) > 4_000:
            raise serializers.ValidationError("Selected text exceeds 4000 characters.")
        return value

    def validate_anchor_data(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Anchor data must be an object.")
        if len(json.dumps(value, ensure_ascii=False)) > 4_096:
            raise serializers.ValidationError("Anchor data exceeds 4096 characters.")
        return value

    def validate(self, attrs):
        lecture = self.context["lecture"]
        page_number = attrs.get("page_number", getattr(self.instance, "page_number", None))
        if page_number is not None and (page_number < 1 or page_number > lecture.page_count):
            raise serializers.ValidationError({"page_number": "Page number is outside the lecture range."})
        return attrs

    @staticmethod
    def idempotency_hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
