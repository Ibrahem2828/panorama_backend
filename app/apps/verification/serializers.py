from django.db import transaction
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile
from apps.accounts.student_number import FACULTY_CODE_LABELS, StudentNumberParser, apply_student_number_parse
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.upload_validation import validate_image_upload
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
    card_image = serializers.ImageField(validators=[validate_image_upload])
    user_name = serializers.CharField(source="user.full_name", read_only=True)
    user_email = serializers.EmailField(source="user.email", read_only=True)
    university_detail = UniversitySerializer(source="university", read_only=True)
    faculty_detail = FacultySerializer(source="faculty", read_only=True)
    major_detail = MajorSerializer(source="major", read_only=True)
    academic_year_detail = AcademicYearSerializer(source="academic_year", read_only=True)
    semester_detail = SemesterSerializer(source="semester", read_only=True)

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

    detected_faculty_code = serializers.SerializerMethodField()
    detected_faculty_name = serializers.SerializerMethodField()
    detected_enrollment_year = serializers.SerializerMethodField()
    detected_serial_number = serializers.SerializerMethodField()

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

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["card_image"] = None
        return data

    def validate(self, attrs):
        request = self.context["request"]
        user = request.user
        if user.role != UserRole.STUDENT:
            raise serializers.ValidationError("Only students can submit verification requests.")
        if not user.is_phone_verified:
            raise serializers.ValidationError("Phone number must be verified before student verification.")
        if VerificationRequest.objects.filter(user=user, status=VerificationStatus.PENDING).exists():
            raise serializers.ValidationError("You already have a pending verification request.")
        if not hasattr(user, "student_profile"):
            raise serializers.ValidationError("Student profile does not exist.")
        try:
            parsed = StudentNumberParser.parse(attrs.get("student_number", ""))
            if attrs.get("faculty") and attrs["faculty"].code in FACULTY_CODE_LABELS and attrs["faculty"].code != parsed["faculty_code"]:
                raise serializers.ValidationError({"student_number": "Student number does not match selected faculty."})
            validate_academic_hierarchy(
                university=attrs.get("university"),
                faculty=attrs.get("faculty"),
                major=attrs.get("major"),
                academic_year=attrs.get("academic_year"),
                semester=attrs.get("semester"),
            )
        except Exception as exc:
            raise serializers.ValidationError(getattr(exc, "message_dict", str(exc))) from exc
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        user = self.context["request"].user
        profile = user.student_profile
        verification = VerificationRequest.objects.create(user=user, student_profile=profile, **validated_data)
        profile.verification_status = StudentVerificationStatus.PENDING
        profile.university = verification.university
        profile.faculty = verification.faculty
        profile.major = verification.major
        profile.academic_year = verification.academic_year
        profile.semester = verification.semester
        profile.student_number = verification.student_number
        profile.card_image = verification.card_image
        apply_student_number_parse(profile, verification.student_number, auto_link_faculty=False)
        profile.save()
        AuditLogService.log(
            actor=user,
            action=AuditAction.VERIFICATION_SUBMITTED,
            target=verification,
            new_value={"status": verification.status, "student_number": verification.student_number},
            request=self.context.get("request"),
        )
        return verification


class VerificationRequestStudentSerializer(VerificationRequestSerializer):
    class Meta(VerificationRequestSerializer.Meta):
        fields = [field for field in VerificationRequestSerializer.Meta.fields if field != "admin_note"]


class VerificationReviewSerializer(serializers.Serializer):
    rejection_reason = serializers.CharField(required=False, allow_blank=True)
    admin_note = serializers.CharField(required=False, allow_blank=True)
