from __future__ import annotations

from rest_framework import serializers

from apps.common.file_validation import validate_image_upload
from apps.universities.models import Subject

from .models import EnrollmentItem, EnrollmentRequest


class SubjectBriefSerializer(serializers.ModelSerializer):
    academic_year_name = serializers.CharField(source="academic_year.name", read_only=True)
    semester_name = serializers.CharField(source="semester.name", read_only=True)
    in_current_term = serializers.SerializerMethodField()

    class Meta:
        model = Subject
        fields = ["id", "name", "code", "academic_year_name", "semester_name", "in_current_term"]
        read_only_fields = fields

    def get_in_current_term(self, obj) -> bool:
        annotated = getattr(obj, "in_current_term", None)
        if annotated is not None:
            return bool(annotated)
        from apps.universities.terms import current_semester_id

        cache = self.context if isinstance(self.context, dict) else {}
        if "_current_semester_id" not in cache:
            cache["_current_semester_id"] = current_semester_id()
        return cache["_current_semester_id"] is not None and obj.semester_id == cache["_current_semester_id"]


class EnrollmentItemSerializer(serializers.ModelSerializer):
    subject = SubjectBriefSerializer(read_only=True)

    class Meta:
        model = EnrollmentItem
        fields = ["id", "subject", "status", "reason"]
        read_only_fields = fields


class EnrollmentRequestSerializer(serializers.ModelSerializer):
    """What the student sees of their own request (never the screenshot URL)."""

    items = EnrollmentItemSerializer(many=True, read_only=True)

    class Meta:
        model = EnrollmentRequest
        fields = ["id", "status", "review_note", "items", "created_at", "reviewed_at"]
        read_only_fields = fields


class EnrollmentCreateSerializer(serializers.Serializer):
    screenshot = serializers.ImageField(write_only=True)
    subject_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=False, max_length=20)

    def to_internal_value(self, data):
        # Multipart forms send ``subject_ids`` as a repeated field (or one comma separated value).
        if hasattr(data, "getlist"):
            values = data.getlist("subject_ids")
            if len(values) == 1 and "," in values[0]:
                values = [part for part in values[0].split(",") if part.strip()]
            data = {"screenshot": data.get("screenshot"), "subject_ids": values}
        return super().to_internal_value(data)

    def validate_screenshot(self, value):
        return validate_image_upload(value, "screenshot")


class DashboardEnrollmentSerializer(serializers.ModelSerializer):
    items = EnrollmentItemSerializer(many=True, read_only=True)
    user_id = serializers.IntegerField(read_only=True)
    user_name = serializers.CharField(source="user.full_name", read_only=True)
    user_email = serializers.EmailField(source="user.email", read_only=True)
    student_number = serializers.CharField(source="student_profile.student_number", read_only=True)
    faculty_name = serializers.CharField(source="faculty.name", read_only=True)
    academic_year_name = serializers.CharField(source="academic_year.name", read_only=True, default=None)
    semester_name = serializers.CharField(source="semester.name", read_only=True, default=None)
    reviewed_by_name = serializers.CharField(source="reviewed_by.full_name", read_only=True, default=None)
    screenshot_ticket_endpoint = serializers.SerializerMethodField()

    class Meta:
        model = EnrollmentRequest
        fields = [
            "id",
            "status",
            "review_note",
            "items",
            "user_id",
            "user_name",
            "user_email",
            "student_number",
            "faculty_name",
            "academic_year_name",
            "semester_name",
            "reviewed_by_name",
            "reviewed_at",
            "created_at",
            "screenshot_ticket_endpoint",
        ]
        read_only_fields = fields

    def get_screenshot_ticket_endpoint(self, obj) -> str:
        return f"/api/v1/dashboard/enrollment/requests/{obj.pk}/screenshot-ticket/"


class DecisionSerializer(serializers.Serializer):
    APPROVE_ALL = "approve_all"
    REJECT_ALL = "reject_all"
    APPROVE_SELECTED = "approve_selected"

    action = serializers.ChoiceField(choices=[APPROVE_ALL, REJECT_ALL, APPROVE_SELECTED])
    item_ids = serializers.ListField(child=serializers.IntegerField(), required=False)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500)

    def validate(self, attrs):
        if attrs["action"] == self.APPROVE_SELECTED and "item_ids" not in attrs:
            raise serializers.ValidationError({"item_ids": "Pick the subjects to approve."})
        return attrs


class BulkDecisionSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=False, max_length=200)
    action = serializers.ChoiceField(choices=[DecisionSerializer.APPROVE_ALL, DecisionSerializer.REJECT_ALL])
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500)
