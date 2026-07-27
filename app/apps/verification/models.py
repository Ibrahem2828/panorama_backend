from __future__ import annotations

import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel


class VerificationStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    NEEDS_UPDATE = "needs_update", "Needs Update"
    CANCELLED = "cancelled", "Cancelled"


class VerificationRequest(BaseModel):
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="verification_requests")
    student_profile = models.ForeignKey(
        "accounts.StudentProfile",
        on_delete=models.CASCADE,
        related_name="verification_requests",
    )
    university = models.ForeignKey("universities.University", on_delete=models.PROTECT, related_name="verification_requests")
    faculty = models.ForeignKey("universities.Faculty", on_delete=models.PROTECT, related_name="verification_requests")
    major = models.ForeignKey("universities.Major", on_delete=models.PROTECT, related_name="verification_requests")
    academic_year = models.ForeignKey(
        "universities.AcademicYear",
        on_delete=models.PROTECT,
        related_name="verification_requests",
    )
    semester = models.ForeignKey(
        "universities.Semester",
        on_delete=models.PROTECT,
        related_name="verification_requests",
        null=True,
        blank=True,
    )
    student_number = models.CharField(max_length=64)
    card_image = models.ImageField(upload_to="verification_cards/")
    status = models.CharField(max_length=32, choices=VerificationStatus.choices, default=VerificationStatus.PENDING)
    rejection_reason = models.TextField(blank=True)
    admin_note = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        related_name="reviewed_verification_requests",
        null=True,
        blank=True,
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["university", "faculty", "major", "academic_year"]),
            models.Index(fields=["student_number"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(status=VerificationStatus.PENDING, is_deleted=False),
                name="unique_pending_verification_per_user",
            )
        ]

    def __str__(self) -> str:
        return f"{self.user_id} - {self.status}"


class VerificationCardAccessTicket(BaseModel):
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    verification = models.ForeignKey(
        VerificationRequest,
        on_delete=models.CASCADE,
        related_name="card_access_tickets",
    )
    requested_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="verification_card_tickets",
    )
    expires_at = models.DateTimeField()
    max_uses = models.PositiveSmallIntegerField(default=3)
    use_count = models.PositiveSmallIntegerField(default=0)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["token", "expires_at"], name="verification_card_token_idx")]

    @property
    def is_valid(self) -> bool:
        return (
            self.revoked_at is None
            and self.expires_at > timezone.now()
            and self.use_count < self.max_uses
            and not self.verification.is_deleted
        )

    @classmethod
    def issue(cls, verification, requested_by):
        return cls.objects.create(
            verification=verification,
            requested_by=requested_by,
            expires_at=timezone.now() + timedelta(seconds=settings.FILE_ACCESS_TICKET_TTL_SECONDS),
            max_uses=3,
        )
