from __future__ import annotations

from django.db import transaction
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.student_number import FACULTY_CODE_LABELS, StudentNumberParser, apply_student_number_parse
from apps.common.file_validation import validate_image_upload
from apps.universities.models import validate_academic_hierarchy
from apps.universities.serializers import (
    AcademicYearSerializer,
    FacultySerializer,
    MajorSerializer,
    SemesterSerializer,
    UniversitySerializer,
)

from .models import VerificationRequest, VerificationStatus


class VerificationRequestSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.full_name", read_only=True)
    user_email = serializers.EmailField(source="user.email", read_only=True)
    university_detail = UniversitySerializer(source="university", read_only=True)
    faculty_detail = FacultySerializer(source="faculty", read_only=True)
    major_detail = MajorSerializer(source="major", read_only=True)
    academic_year_detail = AcademicYearSerializer(source="academic_year", read_only=True)
    semester_detail = SemesterSerializer(source="semester", read_only=True)
    detected_faculty_code = serializers.SerializerMethodField()
    detected_faculty_name = serializers.SerializerMethodField()
    detected_enrollment_year = serializers.SerializerMethodField()
    detected_serial_number = serializers.SerializerMethodField()
    card_image = serializers.ImageField(write_only=True)
    has_card_image = serializers.SerializerMethodField()
    card_ticket_endpoint = serializers.SerializerMethodField()

    class Meta:
        model = VerificationRequest
        fields = [
            "id",
            "user",
            "user_name",
            "user_email",
            "university",
            "university_detail",
            "faculty",
            "faculty_detail",
            "major",
            "major_detail",
            "academic_year",
            "academic_year_detail",
            "semester",
            "semester_detail",
            "student_number",
            "detected_faculty_code",
            "detected_faculty_name",
            "detected_enrollment_year",
            "detected_serial_number",
            "card_image",
            "has_card_image",
            "card_ticket_endpoint",
            "status",
            "rejection_reason",
            "admin_note",
            "reviewed_by",
            "reviewed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "user",
            "status",
            "rejection_reason",
            "admin_note",
            "reviewed_by",
            "reviewed_at",
            "created_at",
            "updated_at",
        ]

    def _parsed(self, obj):
        try:
            return StudentNumberParser.parse(obj.student_number)
        except Exception:
            return {}

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_detected_faculty_code(self, obj):
        return self._parsed(obj).get("faculty_code")

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_detected_faculty_name(self, obj):
        return self._parsed(obj).get("faculty_label")

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_detected_enrollment_year(self, obj):
        return self._parsed(obj).get("enrollment_year_full")

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_detected_serial_number(self, obj):
        return self._parsed(obj).get("serial_number")

    @extend_schema_field(serializers.BooleanField())
    def get_has_card_image(self, obj):
        return bool(obj.card_image)

    @extend_schema_field(serializers.CharField())
    def get_card_ticket_endpoint(self, obj):
        return f"/api/v1/dashboard/verifications/{obj.pk}/card-ticket/"

    def validate_card_image(self, value):
        return validate_image_upload(value, "card_image")

    def validate(self, attrs):
        request = self.context["request"]
        user = request.user
        if user.role != UserRole.STUDENT:
            raise serializers.ValidationError("Only students can submit verification requests.")
        if not (user.is_email_verified or user.is_phone_verified):
            raise serializers.ValidationError("Email or phone must be verified before student verification.")
        if VerificationRequest.objects.filter(user=user, status=VerificationStatus.PENDING, is_deleted=False).exists():
            raise serializers.ValidationError("You already have a pending verification request.")
        if not hasattr(user, "student_profile"):
            raise serializers.ValidationError("Student profile does not exist.")
        try:
            parsed = StudentNumberParser.parse(attrs.get("student_number", ""))
            faculty = attrs.get("faculty")
            if faculty and faculty.code in FACULTY_CODE_LABELS and faculty.code != parsed["faculty_code"]:
                raise serializers.ValidationError({"student_number": "Student number does not match selected faculty."})
            validate_academic_hierarchy(
                university=attrs.get("university"),
                faculty=faculty,
                major=attrs.get("major"),
                academic_year=attrs.get("academic_year"),
                semester=attrs.get("semester"),
            )
        except serializers.ValidationError:
            raise
        except Exception as exc:
            raise serializers.ValidationError(getattr(exc, "message_dict", str(exc))) from exc
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        user = self.context["request"].user
        profile = user.student_profile
        # Lock the profile so two concurrent submissions cannot create two pending states.
        profile.__class__.objects.select_for_update().get(pk=profile.pk)
        verification = VerificationRequest.objects.create(user=user, student_profile=profile, **validated_data)
        profile.verification_status = StudentVerificationStatus.PENDING
        profile.university = verification.university
        profile.faculty = verification.faculty
        profile.major = verification.major
        profile.academic_year = verification.academic_year
        profile.semester = verification.semester
        profile.student_number = verification.student_number
        apply_student_number_parse(profile, verification.student_number, auto_link_faculty=False)
        profile.save()
        return verification


class VerificationRequestStudentSerializer(VerificationRequestSerializer):
    class Meta(VerificationRequestSerializer.Meta):
        fields = [
            field
            for field in VerificationRequestSerializer.Meta.fields
            if field not in {"admin_note", "card_ticket_endpoint"}
        ]


class VerificationReviewSerializer(serializers.Serializer):
    rejection_reason = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    admin_note = serializers.CharField(required=False, allow_blank=True, max_length=4000)
