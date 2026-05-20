import secrets
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel

from .choices import OTPPurpose, StudentVerificationStatus, UserRole
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

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name", "phone_number"]

    class Meta:
        ordering = ["-date_joined"]
        indexes = [
            models.Index(fields=["email"]),
            models.Index(fields=["phone_number"]),
            models.Index(fields=["role"]),
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
    DEFAULT_EXPIRY_MINUTES = 10

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="otp_codes", null=True, blank=True)
    phone_number = models.CharField(max_length=32, db_index=True)
    code_hash = models.CharField(max_length=255)
    purpose = models.CharField(max_length=32, choices=OTPPurpose.choices)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    attempts_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["phone_number", "purpose", "is_used"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.phone_number} - {self.purpose}"

    @staticmethod
    def generate_code(length: int = 6) -> str:
        start = 10 ** (length - 1)
        end = (10**length) - 1
        return str(secrets.randbelow(end - start + 1) + start)

    @classmethod
    def default_expiry(cls):
        return timezone.now() + timedelta(minutes=cls.DEFAULT_EXPIRY_MINUTES)

    def set_code(self, raw_code: str) -> None:
        self.code_hash = make_password(raw_code)

    def verify_code(self, raw_code: str) -> bool:
        if self.is_expired() or self.is_used:
            return False
        is_valid = check_password(raw_code, self.code_hash)
        if not is_valid:
            self.attempts_count += 1
            self.save(update_fields=["attempts_count", "updated_at"])
        return is_valid

    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    def mark_used(self) -> None:
        self.is_used = True
        self.save(update_fields=["is_used", "updated_at"])
