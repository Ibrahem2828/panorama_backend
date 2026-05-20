from django.db import models

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

    def __str__(self) -> str:
        return f"{self.user_id} - {self.status}"
