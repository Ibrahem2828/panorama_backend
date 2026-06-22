import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel

from .choices import StudentAccountRequestStatus


class StudentAccountRequest(BaseModel):
    OTP_EXPIRY_MINUTES = 10

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    full_name = models.CharField(max_length=255)
    email = models.EmailField(db_index=True)
    phone_number = models.CharField(max_length=32, db_index=True)
    university = models.ForeignKey(
        "universities.University",
        on_delete=models.PROTECT,
        related_name="student_account_requests",
    )
    faculty = models.ForeignKey(
        "universities.Faculty",
        on_delete=models.SET_NULL,
        related_name="student_account_requests",
        null=True,
        blank=True,
    )
    major = models.ForeignKey(
        "universities.Major",
        on_delete=models.SET_NULL,
        related_name="student_account_requests",
        null=True,
        blank=True,
    )
    student_number = models.CharField(max_length=64, db_index=True)
    password_hash = models.CharField(max_length=255)
    uploaded_card = models.FileField(upload_to="student_account_requests/")
    status = models.CharField(
        max_length=32,
        choices=StudentAccountRequestStatus.choices,
        default=StudentAccountRequestStatus.PENDING_REVIEW,
        db_index=True,
    )
    admin_note = models.TextField(blank=True)
    rejection_reason = models.TextField(blank=True)
    needs_update_reason = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        related_name="reviewed_student_account_requests",
        null=True,
        blank=True,
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    created_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        related_name="student_account_request",
        null=True,
        blank=True,
    )
    otp_hash = models.CharField(max_length=255, blank=True)
    otp_expires_at = models.DateTimeField(null=True, blank=True)
    otp_attempt_count = models.PositiveSmallIntegerField(default=0)
    otp_resend_count = models.PositiveSmallIntegerField(default=0)
    otp_last_sent_at = models.DateTimeField(null=True, blank=True)
    otp_verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["email", "status"]),
            models.Index(fields=["phone_number", "status"]),
            models.Index(fields=["university", "student_number"]),
        ]

    def __str__(self) -> str:
        return f"{self.full_name} - {self.status}"

    @classmethod
    def open_statuses(cls):
        return {
            StudentAccountRequestStatus.PENDING_REVIEW,
            StudentAccountRequestStatus.APPROVED_PENDING_OTP,
            StudentAccountRequestStatus.OTP_SENT,
        }

    @classmethod
    def otp_eligible_statuses(cls):
        return {
            StudentAccountRequestStatus.APPROVED_PENDING_OTP,
            StudentAccountRequestStatus.OTP_SENT,
        }

    def set_password(self, raw_password: str) -> None:
        self.password_hash = make_password(raw_password)

    def set_otp(self, raw_code: str) -> None:
        self.otp_hash = make_password(raw_code)
        self.otp_expires_at = timezone.now() + timedelta(minutes=self.OTP_EXPIRY_MINUTES)
        self.otp_attempt_count = 0
        self.otp_last_sent_at = timezone.now()

    def verify_otp_code(self, raw_code: str) -> bool:
        if not self.otp_hash or not self.otp_expires_at:
            return False
        if self.is_otp_expired():
            return False
        if self.otp_attempt_count >= settings.MAX_OTP_VERIFY_ATTEMPTS:
            return False
        is_valid = check_password(raw_code, self.otp_hash)
        if not is_valid:
            self.otp_attempt_count += 1
            self.save(update_fields=["otp_attempt_count", "updated_at"])
        return is_valid

    def is_otp_expired(self) -> bool:
        if self.otp_expires_at is None:
            return True
        return timezone.now() >= self.otp_expires_at

    def clear_otp(self) -> None:
        self.otp_hash = ""
        self.otp_expires_at = None
        self.otp_attempt_count = 0

    @staticmethod
    def generate_otp_code() -> str:
        from .models import OTPCode

        return OTPCode.generate_code()