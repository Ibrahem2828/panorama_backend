from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import models

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
