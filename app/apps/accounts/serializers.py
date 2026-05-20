from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.db.models import Q
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from apps.universities.models import validate_academic_hierarchy
from apps.universities.serializers import (
    AcademicYearSerializer,
    FacultySerializer,
    MajorSerializer,
    SemesterSerializer,
    UniversitySerializer,
)

from .choices import OTPPurpose, StudentVerificationStatus, UserRole
from .models import StudentProfile, User
from .services import OTPService
from .student_number import StudentNumberParser, apply_student_number_parse
from .student_number import FACULTY_CODE_LABELS


class StudentProfileSerializer(serializers.ModelSerializer):
    university_detail = UniversitySerializer(source="university", read_only=True)
    faculty_detail = FacultySerializer(source="faculty", read_only=True)
    major_detail = MajorSerializer(source="major", read_only=True)
    academic_year_detail = AcademicYearSerializer(source="academic_year", read_only=True)
    semester_detail = SemesterSerializer(source="semester", read_only=True)

    class Meta:
        model = StudentProfile
        fields = [
            "id",
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
            "faculty_code_from_student_number",
            "enrollment_year_code",
            "enrollment_year_full",
            "student_serial_number",
            "card_image",
            "verification_status",
            "verified_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "faculty_code_from_student_number",
            "enrollment_year_code",
            "enrollment_year_full",
            "student_serial_number",
            "verification_status",
            "verified_at",
            "created_at",
            "updated_at",
        ]


class StudentAcademicProfileSerializer(StudentProfileSerializer):
    editable_statuses = {
        StudentVerificationStatus.INCOMPLETE,
        StudentVerificationStatus.REJECTED,
        StudentVerificationStatus.NEEDS_UPDATE,
    }

    def validate(self, attrs):
        instance = self.instance
        if instance and instance.verification_status not in self.editable_statuses:
            blocked_fields = {
                "university",
                "faculty",
                "major",
                "academic_year",
                "semester",
                "student_number",
                "card_image",
            }
            if blocked_fields.intersection(attrs):
                raise serializers.ValidationError(
                    "Academic profile cannot be changed after approved or pending verification."
                )

        university = attrs.get("university") or getattr(instance, "university", None)
        faculty = attrs.get("faculty") or getattr(instance, "faculty", None)
        major = attrs.get("major") or getattr(instance, "major", None)
        academic_year = attrs.get("academic_year") or getattr(instance, "academic_year", None)
        semester = attrs.get("semester") or getattr(instance, "semester", None)
        try:
            validate_academic_hierarchy(
                university=university,
                faculty=faculty,
                major=major,
                academic_year=academic_year,
                semester=semester,
            )
        except Exception as exc:
            raise serializers.ValidationError(getattr(exc, "message_dict", str(exc))) from exc
        student_number = attrs.get("student_number")
        if student_number:
            try:
                parsed = StudentNumberParser.parse(student_number)
            except Exception as exc:
                raise serializers.ValidationError({"student_number": getattr(exc, "message", str(exc))}) from exc
            faculty = attrs.get("faculty") or getattr(instance, "faculty", None)
            if faculty and faculty.code in FACULTY_CODE_LABELS and faculty.code != parsed["faculty_code"]:
                raise serializers.ValidationError({"student_number": "Student number does not match selected faculty."})
        return attrs

    def update(self, instance, validated_data):
        instance = super().update(instance, validated_data)
        if "student_number" in validated_data and instance.student_number:
            apply_student_number_parse(instance, instance.student_number, auto_link_faculty=True)
            instance.save()
        return instance


class UserSerializer(serializers.ModelSerializer):
    student_verification_status = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "full_name",
            "username",
            "email",
            "phone_number",
            "role",
            "is_phone_verified",
            "is_email_verified",
            "student_verification_status",
            "date_joined",
        ]
        read_only_fields = [
            "id",
            "email",
            "phone_number",
            "role",
            "is_phone_verified",
            "is_email_verified",
            "student_verification_status",
            "date_joined",
        ]

    def get_student_verification_status(self, obj: User) -> str | None:
        profile = getattr(obj, "student_profile", None)
        return profile.verification_status if profile else None

    def validate_username(self, value: str | None) -> str | None:
        if value == "":
            return None
        return value


class BaseRegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, validators=[validate_password])
    password_confirm = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ["full_name", "email", "phone_number", "password", "password_confirm"]

    def validate_email(self, value: str) -> str:
        email = value.lower()
        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return email

    def validate_phone_number(self, value: str) -> str:
        phone_number = value.strip()
        if User.objects.filter(phone_number=phone_number).exists():
            raise serializers.ValidationError("A user with this phone number already exists.")
        return phone_number

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError({"password_confirm": "Passwords do not match."})
        return attrs

    def _create_user(self, validated_data, role: str) -> User:
        validated_data.pop("password_confirm")
        password = validated_data.pop("password")
        return User.objects.create_user(password=password, role=role, **validated_data)


class NormalUserRegisterSerializer(BaseRegisterSerializer):
    @transaction.atomic
    def create(self, validated_data):
        user = self._create_user(validated_data, UserRole.NORMAL_USER)
        otp, raw_code = OTPService.send_otp(user.phone_number, OTPPurpose.VERIFY_PHONE, user=user)
        return user, raw_code


class StudentRegisterSerializer(BaseRegisterSerializer):
    student_number = serializers.CharField(required=False, allow_blank=True, max_length=64)
    card_image = serializers.ImageField(required=False, allow_null=True)

    class Meta(BaseRegisterSerializer.Meta):
        fields = BaseRegisterSerializer.Meta.fields + ["student_number", "card_image"]

    def validate_student_number(self, value: str) -> str:
        value = value.strip()
        if value:
            try:
                StudentNumberParser.validate(value)
            except Exception as exc:
                raise serializers.ValidationError(getattr(exc, "message", str(exc))) from exc
        return value

    @transaction.atomic
    def create(self, validated_data):
        student_number = validated_data.pop("student_number", "")
        card_image = validated_data.pop("card_image", None)
        user = self._create_user(validated_data, UserRole.STUDENT)
        verification_status = (
            StudentVerificationStatus.PENDING if card_image else StudentVerificationStatus.INCOMPLETE
        )
        StudentProfile.objects.create(
            user=user,
            student_number=student_number,
            card_image=card_image,
            verification_status=verification_status,
        )
        if student_number:
            profile = user.student_profile
            apply_student_number_parse(profile, student_number, auto_link_faculty=True)
            profile.save()
        otp, raw_code = OTPService.send_otp(user.phone_number, OTPPurpose.VERIFY_PHONE, user=user)
        return user, raw_code


class LoginSerializer(serializers.Serializer):
    identifier = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        identifier = attrs["identifier"].strip()
        password = attrs["password"]

        user = User.objects.filter(Q(email__iexact=identifier) | Q(phone_number=identifier)).first()
        if user is None or not user.check_password(password):
            raise serializers.ValidationError({"identifier": "Invalid credentials."})
        if not user.is_active:
            raise serializers.ValidationError({"identifier": "This account is inactive."})

        refresh = RefreshToken.for_user(user)
        attrs["user"] = user
        attrs["access"] = str(refresh.access_token)
        attrs["refresh"] = str(refresh)
        return attrs


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, validators=[validate_password])
    new_password_confirm = serializers.CharField(write_only=True)

    def validate_old_password(self, value: str) -> str:
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Old password is incorrect.")
        return value

    def validate(self, attrs):
        if attrs["new_password"] != attrs["new_password_confirm"]:
            raise serializers.ValidationError({"new_password_confirm": "Passwords do not match."})
        return attrs

    def save(self, **kwargs):
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.save(update_fields=["password", "updated_at"])
        return user


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class SendOTPSerializer(serializers.Serializer):
    phone_number = serializers.CharField()
    purpose = serializers.ChoiceField(choices=OTPPurpose.choices)

    def save(self, **kwargs):
        phone_number = self.validated_data["phone_number"].strip()
        user = User.objects.filter(phone_number=phone_number).first()
        return OTPService.send_otp(phone_number, self.validated_data["purpose"], user=user)


class VerifyOTPSerializer(serializers.Serializer):
    phone_number = serializers.CharField()
    code = serializers.CharField(max_length=6, min_length=6)
    purpose = serializers.ChoiceField(choices=OTPPurpose.choices)

    def save(self, **kwargs):
        phone_number = self.validated_data["phone_number"].strip()
        otp = OTPService.verify_otp(
            phone_number=phone_number,
            code=self.validated_data["code"],
            purpose=self.validated_data["purpose"],
        )
        if otp.purpose == OTPPurpose.VERIFY_PHONE and otp.user:
            otp.user.is_phone_verified = True
            otp.user.save(update_fields=["is_phone_verified", "updated_at"])
        return otp


class RequestPasswordResetSerializer(serializers.Serializer):
    phone_number = serializers.CharField()

    def save(self, **kwargs):
        phone_number = self.validated_data["phone_number"].strip()
        user = User.objects.filter(phone_number=phone_number).first()
        if user is None:
            raise serializers.ValidationError({"phone_number": "No account exists with this phone number."})
        return OTPService.send_otp(phone_number, OTPPurpose.RESET_PASSWORD, user=user)


class ConfirmPasswordResetSerializer(serializers.Serializer):
    phone_number = serializers.CharField()
    code = serializers.CharField(max_length=6, min_length=6)
    new_password = serializers.CharField(write_only=True, validators=[validate_password])
    new_password_confirm = serializers.CharField(write_only=True)

    def validate(self, attrs):
        if attrs["new_password"] != attrs["new_password_confirm"]:
            raise serializers.ValidationError({"new_password_confirm": "Passwords do not match."})
        return attrs

    @transaction.atomic
    def save(self, **kwargs):
        phone_number = self.validated_data["phone_number"].strip()
        otp = OTPService.verify_otp(
            phone_number=phone_number,
            code=self.validated_data["code"],
            purpose=OTPPurpose.RESET_PASSWORD,
        )
        user = otp.user or User.objects.filter(phone_number=phone_number).first()
        if user is None:
            raise serializers.ValidationError({"phone_number": "No account exists with this phone number."})
        user.set_password(self.validated_data["new_password"])
        user.save(update_fields=["password", "updated_at"])
        return user
