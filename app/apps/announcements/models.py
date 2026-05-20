from django.db import models

from apps.common.models import BaseModel
from apps.universities.models import validate_academic_hierarchy


class AnnouncementTargetUserType(models.TextChoices):
    ALL = "all", "All"
    NORMAL_USERS = "normal_users", "Normal Users"
    STUDENTS = "students", "Students"
    VERIFIED_STUDENTS = "verified_students", "Verified Students"
    ADMINS = "admins", "Admins"


class Announcement(BaseModel):
    title = models.CharField(max_length=255)
    description = models.TextField()
    image = models.ImageField(upload_to="announcements/", null=True, blank=True)
    link = models.URLField(blank=True)
    target_user_type = models.CharField(max_length=32, choices=AnnouncementTargetUserType.choices, default=AnnouncementTargetUserType.ALL)
    target_university = models.ForeignKey("universities.University", on_delete=models.SET_NULL, related_name="announcements", null=True, blank=True)
    target_faculty = models.ForeignKey("universities.Faculty", on_delete=models.SET_NULL, related_name="announcements", null=True, blank=True)
    target_major = models.ForeignKey("universities.Major", on_delete=models.SET_NULL, related_name="announcements", null=True, blank=True)
    target_academic_year = models.ForeignKey("universities.AcademicYear", on_delete=models.SET_NULL, related_name="announcements", null=True, blank=True)
    target_semester = models.ForeignKey("universities.Semester", on_delete=models.SET_NULL, related_name="announcements", null=True, blank=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="created_announcements")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["is_active", "starts_at", "ends_at"]),
            models.Index(fields=["target_user_type"]),
            models.Index(fields=["target_university", "target_faculty", "target_major", "target_academic_year"]),
        ]

    def clean(self):
        validate_academic_hierarchy(
            university=self.target_university,
            faculty=self.target_faculty,
            major=self.target_major,
            academic_year=self.target_academic_year,
            semester=self.target_semester,
        )

    def __str__(self) -> str:
        return self.title
