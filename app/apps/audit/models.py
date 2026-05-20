from django.db import models

from apps.common.models import BaseModel


class AuditAction(models.TextChoices):
    USER_REGISTERED = "user_registered", "User Registered"
    STUDENT_PROFILE_UPDATED = "student_profile_updated", "Student Profile Updated"
    VERIFICATION_SUBMITTED = "verification_submitted", "Verification Submitted"
    VERIFICATION_APPROVED = "verification_approved", "Verification Approved"
    VERIFICATION_REJECTED = "verification_rejected", "Verification Rejected"
    VERIFICATION_NEEDS_UPDATE = "verification_needs_update", "Verification Needs Update"
    GROUP_CREATED = "group_created", "Group Created"
    GROUP_UPDATED = "group_updated", "Group Updated"
    GROUP_DELETED = "group_deleted", "Group Deleted"
    GROUP_MEMBERSHIP_APPROVED = "group_membership_approved", "Group Membership Approved"
    GROUP_MEMBERSHIP_REJECTED = "group_membership_rejected", "Group Membership Rejected"
    GROUP_MEMBERSHIP_BLOCKED = "group_membership_blocked", "Group Membership Blocked"
    FILE_UPLOADED = "file_uploaded", "File Uploaded"
    FILE_UPDATED = "file_updated", "File Updated"
    FILE_DELETED = "file_deleted", "File Deleted"
    ANNOUNCEMENT_CREATED = "announcement_created", "Announcement Created"
    ANNOUNCEMENT_UPDATED = "announcement_updated", "Announcement Updated"
    ANNOUNCEMENT_DELETED = "announcement_deleted", "Announcement Deleted"
    PRINT_ORDER_CREATED = "print_order_created", "Print Order Created"
    PRINT_ORDER_STATUS_CHANGED = "print_order_status_changed", "Print Order Status Changed"
    SUPPORT_TICKET_CREATED = "support_ticket_created", "Support Ticket Created"
    SUPPORT_TICKET_STATUS_CHANGED = "support_ticket_status_changed", "Support Ticket Status Changed"
    MESSAGE_DELETED = "message_deleted", "Message Deleted"


class AuditLog(BaseModel):
    actor = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, related_name="audit_logs", null=True, blank=True)
    action = models.CharField(max_length=64, choices=AuditAction.choices)
    target_type = models.CharField(max_length=100, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["action", "created_at"]),
            models.Index(fields=["actor", "created_at"]),
            models.Index(fields=["target_type", "target_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.action} {self.target_type}:{self.target_id}"
