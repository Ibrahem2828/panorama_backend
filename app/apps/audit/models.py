from django.db import models

from apps.common.models import BaseModel


class AuditAction(models.TextChoices):
    USER_REGISTERED = "user_registered", "User Registered"
    USER_LOGIN_SUCCEEDED = "user_login_succeeded", "User Login Succeeded"
    USER_LOGIN_FAILED = "user_login_failed", "User Login Failed"
    USER_LOGGED_OUT = "user_logged_out", "User Logged Out"
    PASSWORD_CHANGED = "password_changed", "Password Changed"
    PASSWORD_RESET_CONFIRMED = "password_reset_confirmed", "Password Reset Confirmed"
    OTP_VERIFICATION_FAILED = "otp_verification_failed", "OTP Verification Failed"
    STUDENT_PROFILE_UPDATED = "student_profile_updated", "Student Profile Updated"
    VERIFICATION_SUBMITTED = "verification_submitted", "Verification Submitted"
    VERIFICATION_APPROVED = "verification_approved", "Verification Approved"
    VERIFICATION_REJECTED = "verification_rejected", "Verification Rejected"
    VERIFICATION_NEEDS_UPDATE = "verification_needs_update", "Verification Needs Update"
    VERIFICATION_CARD_PREVIEW_TOKEN_CREATED = (
        "verification_card_preview_token_created",
        "Verification Card Preview Token Created",
    )
    GROUP_CREATED = "group_created", "Group Created"
    GROUP_UPDATED = "group_updated", "Group Updated"
    GROUP_DELETED = "group_deleted", "Group Deleted"
    GROUP_MEMBERSHIP_APPROVED = "group_membership_approved", "Group Membership Approved"
    GROUP_MEMBERSHIP_REJECTED = "group_membership_rejected", "Group Membership Rejected"
    GROUP_MEMBERSHIP_BLOCKED = "group_membership_blocked", "Group Membership Blocked"
    GROUP_MEMBERSHIP_ROLE_CHANGED = "group_membership_role_changed", "Group Membership Role Changed"
    FILE_UPLOADED = "file_uploaded", "File Uploaded"
    FILE_UPDATED = "file_updated", "File Updated"
    FILE_DELETED = "file_deleted", "File Deleted"
    FILE_ACCESSED = "file_accessed", "File Accessed"
    FILE_PREVIEW_TOKEN_CREATED = "file_preview_token_created", "File Preview Token Created"
    FILE_DOWNLOAD_TOKEN_CREATED = "file_download_token_created", "File Download Token Created"
    ANNOUNCEMENT_CREATED = "announcement_created", "Announcement Created"
    ANNOUNCEMENT_UPDATED = "announcement_updated", "Announcement Updated"
    ANNOUNCEMENT_DELETED = "announcement_deleted", "Announcement Deleted"
    PRINT_ORDER_CREATED = "print_order_created", "Print Order Created"
    PRINT_ORDER_ASSIGNED = "print_order_assigned", "Print Order Assigned"
    PRINT_ORDER_STATUS_CHANGED = "print_order_status_changed", "Print Order Status Changed"
    PRINT_ORDER_NOTE_UPDATED = "print_order_note_updated", "Print Order Note Updated"
    PRINT_FILE_PREVIEW_TOKEN_CREATED = "print_file_preview_token_created", "Print File Preview Token Created"
    SUPPORT_TICKET_CREATED = "support_ticket_created", "Support Ticket Created"
    SUPPORT_TICKET_ASSIGNED = "support_ticket_assigned", "Support Ticket Assigned"
    SUPPORT_TICKET_STATUS_CHANGED = "support_ticket_status_changed", "Support Ticket Status Changed"
    SUPPORT_TICKET_PRIORITY_CHANGED = "support_ticket_priority_changed", "Support Ticket Priority Changed"
    SUPPORT_TICKET_STAFF_REPLY = "support_ticket_staff_reply", "Support Ticket Staff Reply"
    MESSAGE_DELETED = "message_deleted", "Message Deleted"
    MESSAGE_REPORTED = "message_reported", "Message Reported"


class AuditLog(BaseModel):
    actor = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, related_name="audit_logs", null=True, blank=True)
    action = models.CharField(max_length=64, choices=AuditAction.choices)
    target_type = models.CharField(max_length=100, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    request_id = models.CharField(max_length=128, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["action", "created_at"]),
            models.Index(fields=["actor", "created_at"]),
            models.Index(fields=["target_type", "target_id"]),
            models.Index(fields=["request_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.action} {self.target_type}:{self.target_id}"
