from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel
from apps.universities.models import validate_academic_hierarchy


class FileVisibility(models.TextChoices):
    PUBLIC = "public", "Public"
    STUDENTS_ONLY = "students_only", "Students Only"
    VERIFIED_STUDENTS_ONLY = "verified_students_only", "Verified Students Only"
    MAJOR_ONLY = "major_only", "Major Only"
    GROUP_ONLY = "group_only", "Group Only"
    ADMIN_ONLY = "admin_only", "Admin Only"


class FileResource(BaseModel):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    file = models.FileField(upload_to="files/")
    file_type = models.CharField(max_length=32, blank=True)
    file_size = models.PositiveBigIntegerField(default=0)
    pages_count = models.PositiveIntegerField(null=True, blank=True)
    sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    uploaded_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="uploaded_files")
    university = models.ForeignKey("universities.University", on_delete=models.SET_NULL, related_name="files", null=True, blank=True)
    faculty = models.ForeignKey("universities.Faculty", on_delete=models.SET_NULL, related_name="files", null=True, blank=True)
    major = models.ForeignKey("universities.Major", on_delete=models.SET_NULL, related_name="files", null=True, blank=True)
    academic_year = models.ForeignKey(
        "universities.AcademicYear",
        on_delete=models.SET_NULL,
        related_name="files",
        null=True,
        blank=True,
    )
    semester = models.ForeignKey("universities.Semester", on_delete=models.SET_NULL, related_name="files", null=True, blank=True)
    subject = models.ForeignKey("universities.Subject", on_delete=models.SET_NULL, related_name="files", null=True, blank=True)
    group = models.ForeignKey("groups.Group", on_delete=models.SET_NULL, related_name="files", null=True, blank=True)
    visibility = models.CharField(max_length=32, choices=FileVisibility.choices, default=FileVisibility.PUBLIC)
    is_printable = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["visibility", "is_active"]),
            models.Index(fields=["university", "faculty", "major", "academic_year", "semester"]),
            models.Index(fields=["group", "visibility"]),
        ]

    def clean(self):
        validate_academic_hierarchy(
            university=self.university,
            faculty=self.faculty,
            major=self.major,
            academic_year=self.academic_year,
            semester=self.semester,
            subject=self.subject,
        )
        if self.visibility == FileVisibility.GROUP_ONLY and not self.group:
            raise ValidationError({"group": "Group is required for group-only files."})
        if self.visibility == FileVisibility.MAJOR_ONLY and (not self.major or not self.academic_year):
            raise ValidationError({"major": "Major and academic year are required for major-only files."})

    def save(self, *args, **kwargs):
        if self.file:
            self.file_size = getattr(self.file, "size", self.file_size) or 0
            self.file_type = Path(self.file.name).suffix.lower().lstrip(".")[:32]
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.title


class FileAccessPurpose(models.TextChoices):
    VIEW = "view", "View"
    PRINT_PREVIEW = "print_preview", "Print Preview"
    ADMIN_REVIEW = "admin_review", "Admin Review"


class FileAccessTicket(BaseModel):
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    file_resource = models.ForeignKey(FileResource, on_delete=models.CASCADE, related_name="access_tickets")
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="file_access_tickets")
    purpose = models.CharField(max_length=32, choices=FileAccessPurpose.choices, default=FileAccessPurpose.VIEW)
    expires_at = models.DateTimeField()
    max_uses = models.PositiveSmallIntegerField(default=8)
    use_count = models.PositiveSmallIntegerField(default=0)
    revoked_at = models.DateTimeField(null=True, blank=True)
    issued_ip_hash = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["token", "expires_at"], name="files_ticket_token_expiry_idx"),
            models.Index(fields=["user", "file_resource", "created_at"], name="files_ticket_user_file_idx"),
        ]

    @property
    def is_valid(self) -> bool:
        return (
            self.revoked_at is None
            and self.expires_at > timezone.now()
            and self.use_count < self.max_uses
            and self.file_resource.is_active
            and not self.file_resource.is_deleted
        )

    @classmethod
    def issue(cls, file_resource, user, purpose=FileAccessPurpose.VIEW, ip_address: str | None = None):
        ip_hash = hashlib.sha256(ip_address.encode("utf-8")).hexdigest() if ip_address else ""
        return cls.objects.create(
            file_resource=file_resource,
            user=user,
            purpose=purpose,
            expires_at=timezone.now() + timedelta(seconds=settings.FILE_ACCESS_TICKET_TTL_SECONDS),
            max_uses=settings.FILE_ACCESS_TICKET_MAX_USES,
            issued_ip_hash=ip_hash,
        )
