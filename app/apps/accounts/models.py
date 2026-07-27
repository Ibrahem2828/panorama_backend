from __future__ import annotations

import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel

from .choices import (
    OTPDeliveryChannel,
    OTPPurpose,
    PermissionEffect,
    StudentVerificationStatus,
    UserRole,
)
from .managers import UserManager


class User(AbstractBaseUser, PermissionsMixin, BaseModel):
    full_name = models.CharField(max_length=255)
    username = models.CharField(max_length=150, unique=True, null=True, blank=True)
    email = models.EmailField(unique=True)
    phone_number = models.CharField(max_length=32, unique=True)
    role = models.CharField(max_length=32, choices=UserRole.choices, default=UserRole.NORMAL_USER)
    is_phone_verified = models.BooleanField(default=False)
    is_email_verified = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)
    last_password_change_at = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name", "phone_number"]

    class Meta:
        ordering = ["-date_joined"]
        indexes = [
            models.Index(fields=["email"]),
            models.Index(fields=["phone_number"]),
            models.Index(fields=["role"]),
            models.Index(fields=["is_active", "role"], name="accounts_user_active_role_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.full_name} <{self.email}>"


class StudentProfile(BaseModel):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="student_profile")
    university = models.ForeignKey(
        "universities.University",
        on_delete=models.SET_NULL,
        related_name="student_profiles",
        null=True,
        blank=True,
    )
    faculty = models.ForeignKey(
        "universities.Faculty",
        on_delete=models.SET_NULL,
        related_name="student_profiles",
        null=True,
        blank=True,
    )
    major = models.ForeignKey(
        "universities.Major",
        on_delete=models.SET_NULL,
        related_name="student_profiles",
        null=True,
        blank=True,
    )
    academic_year = models.ForeignKey(
        "universities.AcademicYear",
        on_delete=models.SET_NULL,
        related_name="student_profiles",
        null=True,
        blank=True,
    )
    semester = models.ForeignKey(
        "universities.Semester",
        on_delete=models.SET_NULL,
        related_name="student_profiles",
        null=True,
        blank=True,
    )
    student_number = models.CharField(max_length=64, blank=True)
    faculty_code_from_student_number = models.CharField(max_length=8, blank=True)
    enrollment_year_code = models.CharField(max_length=2, blank=True)
    enrollment_year_full = models.PositiveSmallIntegerField(null=True, blank=True)
    student_serial_number = models.CharField(max_length=32, blank=True)
    card_image = models.ImageField(upload_to="student_cards/", null=True, blank=True)
    verification_status = models.CharField(
        max_length=32,
        choices=StudentVerificationStatus.choices,
        default=StudentVerificationStatus.INCOMPLETE,
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    verification_reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="reviewed_student_profiles",
        null=True,
        blank=True,
    )
    verification_reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["student_number"]),
            models.Index(fields=["faculty_code_from_student_number", "enrollment_year_code"]),
            models.Index(fields=["verification_status"]),
            models.Index(fields=["university", "faculty", "major", "academic_year", "semester"]),
        ]

    def __str__(self) -> str:
        return f"{self.user.full_name} - {self.verification_status}"


class OTPCode(BaseModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="otp_codes", null=True, blank=True)
    phone_number = models.CharField(max_length=32, blank=True, default="", db_index=True)
    email = models.EmailField(blank=True, default="", db_index=True)
    delivery_channel = models.CharField(
        max_length=16,
        choices=OTPDeliveryChannel.choices,
        default=OTPDeliveryChannel.EMAIL,
    )
    code_hash = models.CharField(max_length=255)
    purpose = models.CharField(max_length=32, choices=OTPPurpose.choices)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    attempts_count = models.PositiveSmallIntegerField(default=0)
    locked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["email", "purpose", "is_used"], name="accounts_otp_email_purpose_idx"),
            models.Index(fields=["phone_number", "purpose", "is_used"]),
            models.Index(fields=["delivery_channel", "purpose", "created_at"], name="accounts_otp_channel_created"),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.identifier} - {self.purpose}"

    @property
    def identifier(self) -> str:
        return self.email if self.delivery_channel == OTPDeliveryChannel.EMAIL else self.phone_number

    @staticmethod
    def generate_code(length: int = 6) -> str:
        start = 10 ** (length - 1)
        end = (10**length) - 1
        return str(secrets.randbelow(end - start + 1) + start)

    @classmethod
    def default_expiry(cls):
        return timezone.now() + timedelta(minutes=getattr(settings, "OTP_EXPIRY_MINUTES", 10))

    def set_code(self, raw_code: str) -> None:
        self.code_hash = make_password(raw_code)

    def verify_code(self, raw_code: str) -> bool:
        if self.is_expired() or self.is_used or self.is_locked():
            return False
        is_valid = check_password(raw_code, self.code_hash)
        if not is_valid:
            self.attempts_count += 1
            if self.attempts_count >= getattr(settings, "OTP_MAX_ATTEMPTS", 5):
                self.locked_at = timezone.now()
            self.save(update_fields=["attempts_count", "locked_at", "updated_at"])
        return is_valid

    def is_locked(self) -> bool:
        return self.locked_at is not None or self.attempts_count >= getattr(settings, "OTP_MAX_ATTEMPTS", 5)

    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    def mark_used(self) -> None:
        self.is_used = True
        self.save(update_fields=["is_used", "updated_at"])


class UserPermissionOverride(BaseModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="permission_overrides")
    permission_code = models.CharField(max_length=128)
    effect = models.CharField(max_length=16, choices=PermissionEffect.choices)
    expires_at = models.DateTimeField(null=True, blank=True)
    granted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="granted_permission_overrides",
        null=True,
        blank=True,
    )
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["user_id", "permission_code"]
        constraints = [
            models.UniqueConstraint(fields=["user", "permission_code"], name="unique_user_permission_override")
        ]
        indexes = [
            models.Index(fields=["user", "permission_code"], name="accounts_permission_user_code"),
            models.Index(fields=["expires_at"], name="accounts_permission_expiry_idx"),
        ]

    @property
    def is_current(self) -> bool:
        return self.expires_at is None or self.expires_at > timezone.now()

    def __str__(self) -> str:
        return f"{self.user_id}:{self.permission_code}:{self.effect}"
