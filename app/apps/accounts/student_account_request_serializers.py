from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers

from apps.accounts.choices import StudentAccountRequestStatus, StudentVerificationStatus
from apps.accounts.models import StudentProfile, User
from apps.accounts.serializers import validate_phone_number_format
from apps.accounts.student_account_request_models import StudentAccountRequest
from apps.accounts.student_number import StudentNumberParser
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.upload_validation import validate_student_card_upload
from apps.universities.models import Faculty, Major, University, validate_academic_hierarchy
from apps.universities.serializers import FacultySerializer, MajorSerializer, UniversitySerializer


STATUS_PUBLIC_MESSAGES = {
    StudentAccountRequestStatus.PENDING_REVIEW: "طلبك قيد المراجعة من قبل الإدارة.",
    StudentAccountRequestStatus.APPROVED_PENDING_OTP: "تم قبول طلبك. يرجى إدخال رمز التفعيل المرسل إليك.",
    StudentAccountRequestStatus.OTP_SENT: "تم قبول طلبك. يرجى إدخال رمز التفعيل المرسل إليك.",
    StudentAccountRequestStatus.ACTIVE: "تم تفعيل حسابك بنجاح. يمكنك تسجيل الدخول الآن.",
    StudentAccountRequestStatus.REJECTED: "تم رفض طلب إنشاء الحساب.",
    StudentAccountRequestStatus.NEEDS_UPDATE: "طلبك يحتاج إلى تحديث البيانات.",
    StudentAccountRequestStatus.EXPIRED: "انتهت صلاحية طلب إنشاء الحساب.",
}


class StudentAccountRequestCreateSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=255)
    email = serializers.EmailField()
    phone_number = serializers.CharField(max_length=32)
    university = serializers.PrimaryKeyRelatedField(queryset=University.objects.filter(is_deleted=False, is_active=True))
    faculty = serializers.PrimaryKeyRelatedField(
        queryset=Faculty.objects.filter(is_deleted=False, is_active=True),
        required=False,
        allow_null=True,
    )
    major = serializers.PrimaryKeyRelatedField(
        queryset=Major.objects.filter(is_deleted=False, is_active=True),
        required=False,
        allow_null=True,
    )
    student_number = serializers.CharField(max_length=64)
    password = serializers.CharField(write_only=True, validators=[validate_password])
    password_confirm = serializers.CharField(write_only=True)
    uploaded_card = serializers.FileField(validators=[validate_student_card_upload])

    def validate_email(self, value: str) -> str:
        email = value.lower().strip()
        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError("البريد الإلكتروني مستخدم مسبقاً.", code="duplicate_email")
        if StudentAccountRequest.objects.filter(
            email__iexact=email,
            status__in=StudentAccountRequest.open_statuses(),
            is_deleted=False,
        ).exists():
            raise serializers.ValidationError("A pending request already exists for this email.")
        return email

    def validate_phone_number(self, value: str) -> str:
        phone_number = validate_phone_number_format(value)
        if User.objects.filter(phone_number=phone_number).exists():
            raise serializers.ValidationError("رقم الجوال مستخدم مسبقاً.", code="duplicate_phone")
        if StudentAccountRequest.objects.filter(
            phone_number=phone_number,
            status__in=StudentAccountRequest.open_statuses(),
            is_deleted=False,
        ).exists():
            raise serializers.ValidationError("A pending request already exists for this phone number.")
        return phone_number

    def validate_student_number(self, value: str) -> str:
        value = value.strip()
        try:
            StudentNumberParser.validate(value)
        except Exception as exc:
            raise serializers.ValidationError(getattr(exc, "message", str(exc))) from exc
        return value

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError({"password_confirm": "Passwords do not match."})
        validate_academic_hierarchy(
            university=attrs.get("university"),
            faculty=attrs.get("faculty"),
            major=attrs.get("major"),
        )
        university = attrs["university"]
        student_number = attrs["student_number"]
        if StudentAccountRequest.objects.filter(
            university=university,
            student_number=student_number,
            status__in=StudentAccountRequest.open_statuses(),
            is_deleted=False,
        ).exists():
            raise serializers.ValidationError({"student_number": "A pending request already exists for this student number."})
        if StudentProfile.objects.filter(
            university=university,
            student_number=student_number,
            verification_status=StudentVerificationStatus.APPROVED,
        ).exists():
            raise serializers.ValidationError({"student_number": "This student number is already registered for this university."})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        validated_data.pop("password_confirm")
        password = validated_data.pop("password")
        request_obj = StudentAccountRequest(
            full_name=validated_data["full_name"].strip(),
            email=validated_data["email"],
            phone_number=validated_data["phone_number"],
            university=validated_data["university"],
            faculty=validated_data.get("faculty"),
            major=validated_data.get("major"),
            student_number=validated_data["student_number"],
            uploaded_card=validated_data["uploaded_card"],
            status=StudentAccountRequestStatus.PENDING_REVIEW,
        )
        request_obj.set_password(password)
        request_obj.save()
        AuditLogService.log(
            action=AuditAction.STUDENT_ACCOUNT_REQUEST_SUBMITTED,
            target=request_obj,
            new_value={"status": request_obj.status, "request_id": str(request_obj.public_id)},
            request=self.context.get("request"),
        )
        return request_obj


class StudentAccountRequestStatusSerializer(serializers.ModelSerializer):
    request_id = serializers.UUIDField(source="public_id", read_only=True)
    public_message = serializers.SerializerMethodField()
    can_enter_otp = serializers.SerializerMethodField()
    can_resubmit = serializers.SerializerMethodField()
    rejection_reason = serializers.SerializerMethodField()
    needs_update_reason = serializers.SerializerMethodField()

    class Meta:
        model = StudentAccountRequest
        fields = [
            "request_id",
            "status",
            "public_message",
            "rejection_reason",
            "needs_update_reason",
            "can_enter_otp",
            "can_resubmit",
        ]

    def get_public_message(self, obj: StudentAccountRequest) -> str:
        return STATUS_PUBLIC_MESSAGES.get(obj.status, "حالة الطلب غير معروفة.")

    def get_can_enter_otp(self, obj: StudentAccountRequest) -> bool:
        return obj.status in StudentAccountRequest.otp_eligible_statuses()

    def get_can_resubmit(self, obj: StudentAccountRequest) -> bool:
        return False

    def get_rejection_reason(self, obj: StudentAccountRequest) -> str | None:
        if obj.status == StudentAccountRequestStatus.REJECTED:
            return obj.rejection_reason or None
        return None

    def get_needs_update_reason(self, obj: StudentAccountRequest) -> str | None:
        if obj.status == StudentAccountRequestStatus.NEEDS_UPDATE:
            return obj.needs_update_reason or None
        return None


class StudentAccountRequestAdminListSerializer(serializers.ModelSerializer):
    university_detail = UniversitySerializer(source="university", read_only=True)
    faculty_detail = FacultySerializer(source="faculty", read_only=True)
    major_detail = MajorSerializer(source="major", read_only=True)

    class Meta:
        model = StudentAccountRequest
        fields = [
            "id",
            "public_id",
            "full_name",
            "email",
            "phone_number",
            "university",
            "university_detail",
            "faculty",
            "faculty_detail",
            "major",
            "major_detail",
            "student_number",
            "status",
            "reviewed_at",
            "approved_at",
            "activated_at",
            "created_at",
            "updated_at",
        ]


class StudentAccountRequestAdminDetailSerializer(StudentAccountRequestAdminListSerializer):
    reviewed_by_name = serializers.CharField(source="reviewed_by.full_name", read_only=True)
    created_user_id = serializers.IntegerField(source="created_user.id", read_only=True)
    has_uploaded_card = serializers.SerializerMethodField()

    class Meta(StudentAccountRequestAdminListSerializer.Meta):
        fields = StudentAccountRequestAdminListSerializer.Meta.fields + [
            "admin_note",
            "rejection_reason",
            "needs_update_reason",
            "reviewed_by",
            "reviewed_by_name",
            "created_user_id",
            "otp_expires_at",
            "otp_last_sent_at",
            "otp_verified_at",
            "has_uploaded_card",
        ]

    def get_has_uploaded_card(self, obj: StudentAccountRequest) -> bool:
        return bool(obj.uploaded_card)


class StudentAccountRequestApproveSerializer(serializers.Serializer):
    admin_note = serializers.CharField(required=False, allow_blank=True, default="")


class StudentAccountRequestRejectSerializer(serializers.Serializer):
    rejection_reason = serializers.CharField()
    admin_note = serializers.CharField(required=False, allow_blank=True, default="")


class StudentAccountRequestNeedsUpdateSerializer(serializers.Serializer):
    needs_update_reason = serializers.CharField()
    admin_note = serializers.CharField(required=False, allow_blank=True, default="")


class StudentAccountRequestResendOtpSerializer(serializers.Serializer):
    pass


class StudentAccountRequestVerifyOtpSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=6, min_length=6)
