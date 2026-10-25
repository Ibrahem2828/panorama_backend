"""Per-term subject enrollment: a student sends a Learnata screenshot and the subjects they took; staff review it.

Approving a subject adds the student to that subject's group(s). Subject groups can no longer be joined by hand.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel


class EnrollmentStatus(models.TextChoices):
    PENDING = "pending", "Pending review"
    APPROVED = "approved", "Approved"
    PARTIALLY_APPROVED = "partially_approved", "Partially approved"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled"


class EnrollmentItemStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class EnrollmentRequest(BaseModel):
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="enrollment_requests")
    student_profile = models.ForeignKey(
        "accounts.StudentProfile", on_delete=models.CASCADE, related_name="enrollment_requests"
    )
    faculty = models.ForeignKey("universities.Faculty", on_delete=models.PROTECT, related_name="enrollment_requests")
    academic_year = models.ForeignKey(
        "universities.AcademicYear", on_delete=models.PROTECT, null=True, blank=True, related_name="enrollment_requests"
    )
    semester = models.ForeignKey(
        "universities.Semester", on_delete=models.PROTECT, null=True, blank=True, related_name="enrollment_requests"
    )
    #: The Learnata screenshot (name, term, date and the registered subjects). Protected like the student card.
    screenshot = models.ImageField(upload_to="enrollment_screenshots/")
    status = models.CharField(max_length=32, choices=EnrollmentStatus.choices, default=EnrollmentStatus.PENDING)
    review_note = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="reviewed_enrollments"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            # One open request per student: they cancel it or wait for the decision before sending another.
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(status="pending", is_deleted=False),
                name="enrollment_one_pending_per_user",
            )
        ]
        indexes = [
            models.Index(fields=["status", "created_at"], name="enrollment_status_idx"),
            models.Index(fields=["faculty", "status"], name="enrollment_faculty_status_idx"),
        ]


class EnrollmentItem(BaseModel):
    request = models.ForeignKey(EnrollmentRequest, on_delete=models.CASCADE, related_name="items")
    subject = models.ForeignKey("universities.Subject", on_delete=models.PROTECT, related_name="enrollment_items")
    status = models.CharField(max_length=16, choices=EnrollmentItemStatus.choices, default=EnrollmentItemStatus.PENDING)
    reason = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["subject__name", "id"]
        constraints = [models.UniqueConstraint(fields=["request", "subject"], name="enrollment_item_unique")]


class EnrollmentScreenshotTicket(BaseModel):
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    enrollment = models.ForeignKey(EnrollmentRequest, on_delete=models.CASCADE, related_name="screenshot_tickets")
    requested_by = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="enrollment_screenshot_tickets"
    )
    expires_at = models.DateTimeField()
    max_uses = models.PositiveSmallIntegerField(default=5)
    use_count = models.PositiveSmallIntegerField(default=0)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["token", "expires_at"], name="enrollment_shot_token_idx")]

    @property
    def is_valid(self) -> bool:
        return (
            self.revoked_at is None
            and self.expires_at > timezone.now()
            and self.use_count < self.max_uses
            and not self.enrollment.is_deleted
        )

    @classmethod
    def issue(cls, enrollment: EnrollmentRequest, requested_by):
        return cls.objects.create(
            enrollment=enrollment,
            requested_by=requested_by,
            expires_at=timezone.now() + timedelta(seconds=settings.FILE_ACCESS_TICKET_TTL_SECONDS),
        )
