from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel
from apps.universities.models import validate_academic_hierarchy


class Group(BaseModel):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to="groups/", null=True, blank=True)
    university = models.ForeignKey("universities.University", on_delete=models.CASCADE, related_name="groups")
    faculty = models.ForeignKey("universities.Faculty", on_delete=models.SET_NULL, related_name="groups", null=True, blank=True)
    major = models.ForeignKey("universities.Major", on_delete=models.SET_NULL, related_name="groups", null=True, blank=True)
    academic_year = models.ForeignKey(
        "universities.AcademicYear",
        on_delete=models.SET_NULL,
        related_name="groups",
        null=True,
        blank=True,
    )
    semester = models.ForeignKey("universities.Semester", on_delete=models.SET_NULL, related_name="groups", null=True, blank=True)
    subject = models.ForeignKey("universities.Subject", on_delete=models.SET_NULL, related_name="groups", null=True, blank=True)
    created_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="created_groups")
    is_active = models.BooleanField(default=True)
    requires_approval = models.BooleanField(default=True)
    send_messages_permission = models.CharField(
        max_length=32,
        choices=(("all_members", "All Members"), ("admins_only", "Admins Only")),
        default="all_members",
    )

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["university", "faculty", "major", "academic_year", "semester", "is_active"]),
            models.Index(fields=["subject", "is_active"]),
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

    def __str__(self) -> str:
        return self.name


class GroupMembershipRole(models.TextChoices):
    MEMBER = "member", "Member"
    MODERATOR = "moderator", "Moderator"
    GROUP_ADMIN = "group_admin", "Group Admin"


class GroupMembershipStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    BLOCKED = "blocked", "Blocked"
    LEFT = "left", "Left"


class GroupMembership(BaseModel):
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="group_memberships")
    role = models.CharField(max_length=32, choices=GroupMembershipRole.choices, default=GroupMembershipRole.MEMBER)
    status = models.CharField(max_length=32, choices=GroupMembershipStatus.choices, default=GroupMembershipStatus.PENDING)
    reviewed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        related_name="reviewed_group_memberships",
        null=True,
        blank=True,
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    joined_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["group", "user"], name="unique_group_membership")]
        indexes = [
            models.Index(fields=["group", "status"]),
            models.Index(fields=["user", "status"]),
        ]

    def approve(self, reviewer):
        self.status = GroupMembershipStatus.APPROVED
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        self.joined_at = self.joined_at or timezone.now()
        self.save()

    def __str__(self) -> str:
        return f"{self.user_id} -> {self.group_id}: {self.status}"
