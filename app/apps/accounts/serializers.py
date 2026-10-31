from functools import cache
from typing import NoReturn

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.audit.models import AuditAction, LoginFailureReason
from apps.audit.services import AuditLogService
from apps.product.models import ConsentKind
from apps.product.services import PolicyConsentService
from apps.universities.models import validate_academic_hierarchy
from apps.universities.serializers import (
    AcademicYearSerializer,
    FacultySerializer,
    MajorSerializer,
    SemesterSerializer,
    UniversitySerializer,
)

from .authentication import SessionVersionRefreshToken
from .choices import OTPDeliveryChannel, OTPPurpose, StudentVerificationStatus, UserRole
from .lockout import LoginLockoutService
from .models import StudentProfile, User
from .permissions import Capability, PermissionService
from .services import OTPService
from .student_number import (
    FACULTY_CODE_LABELS,
    STUDENT_NUMBER_UNAVAILABLE_MESSAGE,
    StudentNumberParser,
    apply_student_number_parse,
    student_number_in_use,
)


@cache
def _dummy_password_hash() -> str:
    return make_password("panorama-timing-equaliser")


class StudentProfileSerializer(serializers.ModelSerializer):
    has_card_image = serializers.SerializerMethodField()
    university_detail = UniversitySerializer(source="university", read_only=True)
    faculty_detail = FacultySerializer(source="faculty", read_only=True)
    major_detail = MajorSerializer(source="major", read_only=True)
    academic_year_detail = AcademicYearSerializer(source="academic_year", read_only=True)
    semester_detail = SemesterSerializer(source="semester", read_only=True)
    current_term = serializers.SerializerMethodField()

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
            "has_card_image",
            "verification_status",
            "verified_at",
            "needs_term_confirmation",
            "current_term",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "needs_term_confirmation",
            "current_term",
            "faculty_code_from_student_number",
            "enrollment_year_code",
            "enrollment_year_full",
            "student_serial_number",
            "verification_status",
            "verified_at",
            "created_at",
            "updated_at",
        ]

    def get_has_card_image(self, obj) -> bool:
        return bool(obj.card_image)

    @extend_schema_field(serializers.DictField())
    def get_current_term(self, obj) -> dict:
        from apps.universities.terms import current_term_payload

        return current_term_payload()


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
            if student_number_in_use(student_number, exclude_profile_id=getattr(instance, "pk", None)):
                raise serializers.ValidationError(
                    {"student_number": STUDENT_NUMBER_UNAVAILABLE_MESSAGE}, code="student_number_unavailable"
                )
        return attrs

    def update(self, instance, validated_data):
        request = self.context.get("request")
        try:
            with transaction.atomic():
                instance = super().update(instance, validated_data)
                if "student_number" in validated_data and instance.student_number:
                    apply_student_number_parse(instance, instance.student_number, auto_link_faculty=True)
                    instance.save()
                AuditLogService.log(
                    actor=request.user if request is not None else None,
                    action=AuditAction.STUDENT_PROFILE_UPDATED,
                    target=instance,
                    # Field names only: the values (student number, ...) are personal data.
                    new_value={"changed_fields": sorted(validated_data)},
                    request=request,
                    critical=True,
                )
        except IntegrityError as exc:  # lost a race against the unique student-number constraint
            raise serializers.ValidationError(
                {"student_number": STUDENT_NUMBER_UNAVAILABLE_MESSAGE}, code="student_number_unavailable"
            ) from exc
        return instance


class UserSerializer(serializers.ModelSerializer):
    student_verification_status = serializers.SerializerMethodField()
    effective_capabilities = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "full_name",
            "username",
            "job_title",
            "email",
            "phone_number",
            "role",
            "is_phone_verified",
            "is_email_verified",
            "student_verification_status",
            "effective_capabilities",
            "must_change_password",
            "date_joined",
        ]
        read_only_fields = [
            "id",
            "must_change_password",
            "email",
            "phone_number",
            "role",
            "is_phone_verified",
            "is_email_verified",
            "student_verification_status",
            "effective_capabilities",
            "date_joined",
        ]

    def get_student_verification_status(self, obj: User) -> str | None:
        profile = getattr(obj, "student_profile", None)
        return profile.verification_status if profile else None

    def get_effective_capabilities(self, obj: User) -> list[str]:
        codes = [value for name, value in vars(Capability).items() if name.isupper() and isinstance(value, str)]
        return sorted(PermissionService.granted_among(obj, codes))

    def validate_username(self, value: str | None) -> str | None:
        if value == "":
            return None
        return value

    def validate_job_title(self, value: str) -> str:
        return " ".join(str(value or "").split())

    def validate_full_name(self, value: str) -> str:
        value = " ".join(str(value or "").split())
        if not value:
            raise serializers.ValidationError("Name cannot be empty.")
        return value


class BaseRegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, validators=[validate_password])
    password_confirm = serializers.CharField(write_only=True)
    otp_channel = serializers.ChoiceField(
        choices=OTPDeliveryChannel.choices,
        required=False,
        default=OTPDeliveryChannel.EMAIL,
        write_only=True,
    )
    # Optional consent record. Absent (or blank) fields change nothing; when sent they must name the active version.
    accept_terms_version = serializers.CharField(required=False, allow_blank=True, max_length=64, write_only=True)
    accept_privacy_version = serializers.CharField(required=False, allow_blank=True, max_length=64, write_only=True)

    class Meta:
        model = User
        fields = [
            "full_name",
            "email",
            "phone_number",
            "password",
            "password_confirm",
            "otp_channel",
            "accept_terms_version",
            "accept_privacy_version",
        ]

    def _validate_policy_version(self, kind: str, value: str) -> str:
        value = value.strip()
        if value and not PolicyConsentService.is_active_version(kind, value):
            raise serializers.ValidationError("This policy version is not active.")
        return value

    def validate_accept_terms_version(self, value: str) -> str:
        return self._validate_policy_version(ConsentKind.TERMS, value)

    def validate_accept_privacy_version(self, value: str) -> str:
        return self._validate_policy_version(ConsentKind.PRIVACY, value)

    def validate_email(self, value: str) -> str:
        email = value.strip().lower()
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

    def _create_user(self, validated_data, role: str) -> tuple[User, str]:
        validated_data.pop("password_confirm")
        channel = validated_data.pop("otp_channel", OTPDeliveryChannel.EMAIL)
        password = validated_data.pop("password")
        consents = {
            ConsentKind.TERMS: validated_data.pop("accept_terms_version", ""),
            ConsentKind.PRIVACY: validated_data.pop("accept_privacy_version", ""),
        }
        user = User.objects.create_user(password=password, role=role, **validated_data)
        self._record_consents(user, consents)
        return user, channel

    def _record_consents(self, user: User, consents: dict[ConsentKind, str]) -> None:
        """Store the acceptances sent with the registration, in the registration transaction."""
        accepted = [
            PolicyConsentService.record(user, kind=kind, version=version).kind
            for kind, version in consents.items()
            if version
        ]
        if accepted:
            AuditLogService.log(
                actor=user,
                action=AuditAction.POLICY_ACCEPTED,
                new_value={"kinds": accepted, "source": "registration"},
                request=self.context.get("request"),
                critical=True,
            )

    def _send_identity_otp(self, user: User, channel: str):
        identifier = user.email if channel == OTPDeliveryChannel.EMAIL else user.phone_number
        purpose = OTPPurpose.VERIFY_EMAIL if channel == OTPDeliveryChannel.EMAIL else OTPPurpose.VERIFY_PHONE
        return OTPService.send_otp(identifier, purpose, user=user, channel=channel)

    def _audit_registration(self, user: User, channel: str) -> None:
        # Ids and the OTP channel only: no email, phone or name. Runs inside the registration transaction.
        AuditLogService.log(
            actor=user,
            action=AuditAction.USER_REGISTERED,
            target=user,
            new_value={"role": user.role, "otp_channel": channel},
            request=self.context.get("request"),
            critical=True,
        )


class NormalUserRegisterSerializer(BaseRegisterSerializer):
    @transaction.atomic
    def create(self, validated_data):
        user, channel = self._create_user(validated_data, UserRole.NORMAL_USER)
        otp, raw_code = self._send_identity_otp(user, channel)
        self._audit_registration(user, channel)
        return user, raw_code, channel


class StudentRegisterSerializer(BaseRegisterSerializer):
    student_number = serializers.CharField(required=False, allow_blank=True, max_length=64)

    class Meta(BaseRegisterSerializer.Meta):
        fields = [*BaseRegisterSerializer.Meta.fields, "student_number"]

    def validate_student_number(self, value: str) -> str:
        value = value.strip()
        if value:
            try:
                StudentNumberParser.validate(value)
            except Exception as exc:
                raise serializers.ValidationError(getattr(exc, "message", str(exc))) from exc
            if student_number_in_use(value):
                raise serializers.ValidationError(STUDENT_NUMBER_UNAVAILABLE_MESSAGE, code="student_number_unavailable")
        return value

    @transaction.atomic
    def create(self, validated_data):
        student_number = validated_data.pop("student_number", "")
        user, channel = self._create_user(validated_data, UserRole.STUDENT)
        # Registration never creates a pending verification state. Only an actual
        # VerificationRequest may move the profile to PENDING.
        try:
            with transaction.atomic():
                StudentProfile.objects.create(
                    user=user,
                    student_number=student_number,
                    verification_status=StudentVerificationStatus.INCOMPLETE,
                )
        except IntegrityError as exc:  # lost a race against the unique student-number constraint
            raise serializers.ValidationError(
                {"student_number": STUDENT_NUMBER_UNAVAILABLE_MESSAGE}, code="student_number_unavailable"
            ) from exc
        if student_number:
            profile = user.student_profile
            apply_student_number_parse(profile, student_number, auto_link_faculty=True)
            profile.save()
        otp, raw_code = self._send_identity_otp(user, channel)
        self._audit_registration(user, channel)
        transaction.on_commit(lambda: _notify_student_registered(user.pk))
        return user, raw_code, channel


def _notify_student_registered(user_id: int) -> None:
    """Tell verification reviewers a new student signed up (best effort: never fails the registration)."""

    try:
        from apps.notifications.staff import notify_staff

        student = User.objects.filter(pk=user_id).first()
        if student is None:
            return
        notify_staff(
            "student_registration",
            "طالب جديد سجّل في التطبيق",
            f"{student.full_name} أنشأ حساباً جديداً وسيرسل بياناته للتحقق.",
            object_id=student.pk,
            actor_id=student.pk,
        )
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).exception("student_registration_notification_failed")


_INVISIBLE_EDGE_CHARS = "​‌‍⁠﻿"


def _strip_pasted(password: str) -> str:
    """Whitespace and zero-width characters that sneak in when a password is copied out of an e-mail or a chat."""

    return password.strip().strip(_INVISIBLE_EDGE_CHARS).strip()


def find_login_user(identifier: str) -> User | None:
    identifier = (identifier or "").strip()
    if not identifier:
        return None
    return User.objects.filter(Q(email__iexact=identifier) | Q(phone_number=identifier)).first()


class LoginSerializer(serializers.Serializer):
    identifier = serializers.CharField()
    # Not trimmed by DRF: the exact value is tried first, then once more with pasted edge whitespace removed.
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    # Set during validation so the view can feed the per-account lockout without re-checking credentials.
    credentials_failed = False
    matched_user: User | None = None
    # Why the login failed (a ``LoginFailureReason`` value). Recorded in the admin activity log only; the HTTP
    # response stays one generic message so the endpoint cannot be used to enumerate accounts.
    failure_reason: str = ""

    def _fail(self, reason: str, message: str, *, credentials: bool) -> NoReturn:
        self.failure_reason = reason
        self.credentials_failed = credentials
        raise serializers.ValidationError({"identifier": message})

    def validate(self, attrs):
        identifier = attrs["identifier"].strip()
        password = attrs["password"]

        user = find_login_user(identifier)
        self.matched_user = user
        candidates = [password]
        stripped = _strip_pasted(password)
        if stripped and stripped != password:
            candidates.append(stripped)
        password_ok = False
        for candidate in candidates:
            if user is None:
                # Spend the same hashing time as a real check so response timing does not reveal accounts.
                check_password(candidate, _dummy_password_hash())
            elif not password_ok:
                password_ok = user.check_password(candidate)
        # Use one generic failure response to prevent account enumeration; the true reason is kept for admins.
        if user is None:
            self._fail(LoginFailureReason.INVALID_CREDENTIALS, "Invalid credentials.", credentials=True)
        if not password_ok:
            reason = (
                LoginFailureReason.INVALID_CREDENTIALS
                if user.has_usable_password()
                else LoginFailureReason.PASSWORD_NOT_SET
            )
            self._fail(reason, "Invalid credentials.", credentials=True)
        if not user.is_active:
            self._fail(LoginFailureReason.INACTIVE_ACCOUNT, "Invalid credentials.", credentials=True)
        if not (user.is_email_verified or user.is_phone_verified):
            self._fail(
                LoginFailureReason.UNVERIFIED, "Account verification is required before login.", credentials=False
            )

        refresh = SessionVersionRefreshToken.for_user(user)
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
        request = self.context["request"]
        user = request.user
        with transaction.atomic():
            user.set_password(self.validated_data["new_password"])
            user.last_password_change_at = timezone.now()
            user.must_change_password = False
            user.save(update_fields=["password", "last_password_change_at", "must_change_password", "updated_at"])
            user.invalidate_sessions()
            AuditLogService.log(
                actor=user,
                action=AuditAction.PASSWORD_CHANGED,
                target=user,
                new_value={"sessions_revoked": True},
                request=request,
                critical=True,
            )
        return user


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class OTPInputSerializer(serializers.Serializer):
    identifier = serializers.CharField(required=False)
    email = serializers.EmailField(required=False)
    phone_number = serializers.CharField(required=False)
    channel = serializers.ChoiceField(choices=OTPDeliveryChannel.choices, required=False)

    def validate(self, attrs):
        channel = attrs.get("channel")
        identifier = attrs.get("identifier") or attrs.get("email") or attrs.get("phone_number")
        if not identifier:
            raise serializers.ValidationError({"identifier": "Email or phone number is required."})
        if not channel:
            channel = OTPDeliveryChannel.EMAIL if "@" in str(identifier) else OTPDeliveryChannel.PHONE
        if channel == OTPDeliveryChannel.EMAIL:
            identifier = serializers.EmailField().run_validation(identifier).lower()
        else:
            identifier = str(identifier).strip()
        attrs["identifier"] = identifier
        attrs["channel"] = channel
        return attrs


class SendOTPSerializer(OTPInputSerializer):
    purpose = serializers.ChoiceField(
        choices=[OTPPurpose.VERIFY_EMAIL, OTPPurpose.VERIFY_PHONE],
    )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        expected = OTPPurpose.VERIFY_EMAIL if attrs["channel"] == OTPDeliveryChannel.EMAIL else OTPPurpose.VERIFY_PHONE
        if attrs.get("purpose") != expected:
            raise serializers.ValidationError({"purpose": "Purpose does not match the selected delivery channel."})
        return attrs

    def save(self, **kwargs):
        identifier = self.validated_data["identifier"]
        channel = self.validated_data["channel"]
        user = (
            User.objects.filter(email__iexact=identifier).first()
            if channel == OTPDeliveryChannel.EMAIL
            else User.objects.filter(phone_number=identifier).first()
        )
        # Generic no-op prevents this public endpoint from becoming an arbitrary email/SMS relay.
        if user is None or not user.is_active:
            return None, None
        already_verified = user.is_email_verified if channel == OTPDeliveryChannel.EMAIL else user.is_phone_verified
        if already_verified:
            return None, None
        return OTPService.send_otp(identifier, self.validated_data["purpose"], user=user, channel=channel)


class VerifyOTPSerializer(OTPInputSerializer):
    code = serializers.CharField(max_length=6, min_length=6)
    purpose = serializers.ChoiceField(choices=OTPPurpose.choices)

    def save(self, **kwargs):
        otp = OTPService.verify_otp(
            identifier=self.validated_data["identifier"],
            code=self.validated_data["code"],
            purpose=self.validated_data["purpose"],
            channel=self.validated_data["channel"],
        )
        verifying = otp.purpose in {
            OTPPurpose.VERIFY_EMAIL,
            OTPPurpose.VERIFY_PHONE,
            OTPPurpose.REGISTER,
        }
        if otp.user and verifying:
            if otp.delivery_channel == OTPDeliveryChannel.EMAIL:
                otp.user.is_email_verified = True
                otp.user.save(update_fields=["is_email_verified", "updated_at"])
            else:
                otp.user.is_phone_verified = True
                otp.user.save(update_fields=["is_phone_verified", "updated_at"])
        return otp


class RequestPasswordResetSerializer(OTPInputSerializer):
    def save(self, **kwargs):
        identifier = self.validated_data["identifier"]
        channel = self.validated_data["channel"]
        user = (
            User.objects.filter(email__iexact=identifier).first()
            if channel == OTPDeliveryChannel.EMAIL
            else User.objects.filter(phone_number=identifier).first()
        )
        # Deliberately return a generic successful flow when no account exists, and also when a code
        # was just sent (cooldown), so the response never tells an attacker the account is real.
        if user is None or not user.is_active:
            return None, None
        try:
            return OTPService.send_otp(identifier, OTPPurpose.RESET_PASSWORD, user=user, channel=channel)
        except serializers.ValidationError as exc:
            if "identifier" in getattr(exc, "detail", {}):
                return None, None
            raise


class ConfirmPasswordResetSerializer(OTPInputSerializer):
    code = serializers.CharField(max_length=6, min_length=6)
    new_password = serializers.CharField(write_only=True, validators=[validate_password])
    new_password_confirm = serializers.CharField(write_only=True)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs["new_password"] != attrs["new_password_confirm"]:
            raise serializers.ValidationError({"new_password_confirm": "Passwords do not match."})
        return attrs

    def save(self, **kwargs):
        identifier = self.validated_data["identifier"]
        channel = self.validated_data["channel"]
        # Verified outside any transaction of ours so a wrong guess keeps its attempt count.
        otp = OTPService.verify_otp(
            identifier=identifier,
            code=self.validated_data["code"],
            purpose=OTPPurpose.RESET_PASSWORD,
            channel=channel,
        )
        user = otp.user
        if user is None:
            raise serializers.ValidationError({"code": "Invalid or expired OTP code."})
        with transaction.atomic():
            user.set_password(self.validated_data["new_password"])
            user.last_password_change_at = timezone.now()
            user.save(update_fields=["password", "last_password_change_at", "updated_at"])
            user.invalidate_sessions()
            AuditLogService.log(
                actor=user,
                action=AuditAction.PASSWORD_RESET_COMPLETED,
                target=user,
                new_value={"sessions_revoked": True, "channel": channel},
                request=self.context.get("request"),
                critical=True,
            )
        # The owner proved control of the mailbox/phone, so a brute-force lockout on this account ends here.
        LoginLockoutService.clear_for_user(user)
        return user
