from django.db import models

from apps.common.models import BaseModel


class AuditAction(models.TextChoices):
    USER_REGISTERED = "user_registered", "User Registered"
    USER_ROLE_CHANGED = "user_role_changed", "User Role Changed"
    USER_STATUS_CHANGED = "user_status_changed", "User Status Changed"
    USER_PERMISSION_OVERRIDE_CHANGED = "user_permission_override_changed", "User Permission Override Changed"
    STUDENT_PROFILE_UPDATED = "student_profile_updated", "Student Profile Updated"
    OTP_SENT = "otp_sent", "OTP Sent"
    OTP_VERIFIED = "otp_verified", "OTP Verified"
    OTP_FAILED = "otp_failed", "OTP Failed"
    VERIFICATION_SUBMITTED = "verification_submitted", "Verification Submitted"
    VERIFICATION_APPROVED = "verification_approved", "Verification Approved"
    VERIFICATION_REJECTED = "verification_rejected", "Verification Rejected"
    VERIFICATION_NEEDS_UPDATE = "verification_needs_update", "Verification Needs Update"
    VERIFICATION_CARD_ACCESSED = "verification_card_accessed", "Verification Card Accessed"
    GROUP_CREATED = "group_created", "Group Created"
    GROUP_UPDATED = "group_updated", "Group Updated"
    GROUP_DELETED = "group_deleted", "Group Deleted"
    GROUP_MEMBERSHIP_APPROVED = "group_membership_approved", "Group Membership Approved"
    GROUP_MEMBERSHIP_REJECTED = "group_membership_rejected", "Group Membership Rejected"
    GROUP_MEMBERSHIP_BLOCKED = "group_membership_blocked", "Group Membership Blocked"
    GROUP_EXTERNAL_CHANNEL_UPDATED = "group_external_channel_updated", "External Channel Updated"
    GROUP_EXTERNAL_CHANNEL_TICKET_ISSUED = "group_external_channel_ticket_issued", "External Channel Ticket Issued"
    GROUP_EXTERNAL_CHANNEL_OPENED = "group_external_channel_opened", "External Channel Opened"
    FILE_UPLOADED = "file_uploaded", "File Uploaded"
    FILE_UPDATED = "file_updated", "File Updated"
    FILE_DELETED = "file_deleted", "File Deleted"
    FILE_ACCESS_TICKET_ISSUED = "file_access_ticket_issued", "File Access Ticket Issued"
    LECTURE_UPLOADED = "lecture_uploaded", "Lecture Uploaded"
    LECTURE_PROCESSING_UPDATED = "lecture_processing_updated", "Lecture Processing Updated"
    LECTURE_VIEWED = "lecture_viewed", "Lecture Viewed"
    LECTURE_NOTE_UPDATED = "lecture_note_updated", "Lecture Note Updated"
    ANNOUNCEMENT_CREATED = "announcement_created", "Announcement Created"
    ANNOUNCEMENT_UPDATED = "announcement_updated", "Announcement Updated"
    ANNOUNCEMENT_DELETED = "announcement_deleted", "Announcement Deleted"
    PRINT_ORDER_CREATED = "print_order_created", "Print Order Created"
    PRINT_ORDER_STATUS_CHANGED = "print_order_status_changed", "Print Order Status Changed"
    PRINT_ORDER_ASSIGNED = "print_order_assigned", "Print Order Assigned"
    PRINT_ORDER_NOTE_UPDATED = "print_order_note_updated", "Print Order Note Updated"
    PRINT_PRICING_CHANGED = "print_pricing_changed", "Print Pricing Changed"
    SUPPORT_TICKET_CREATED = "support_ticket_created", "Support Ticket Created"
    SUPPORT_TICKET_STATUS_CHANGED = "support_ticket_status_changed", "Support Ticket Status Changed"
    SUPPORT_TICKET_ASSIGNED = "support_ticket_assigned", "Support Ticket Assigned"
    SUPPORT_TICKET_PRIORITY_CHANGED = "support_ticket_priority_changed", "Support Ticket Priority Changed"
    MESSAGE_DELETED = "message_deleted", "Message Deleted"
    FEEDBACK_SUBMITTED = "feedback_submitted", "Feedback Submitted"
    FEEDBACK_WORKFLOW_UPDATED = "feedback_workflow_updated", "Feedback Workflow Updated"
    FEEDBACK_PRIVACY_REQUESTED = "feedback_privacy_requested", "Feedback Privacy Requested"
    MOBILE_RELEASE_POLICY_UPDATED = "mobile_release_policy_updated", "Mobile Release Policy Updated"
    MAINTENANCE_MODE_UPDATED = "maintenance_mode_updated", "Maintenance Mode Updated"
    FEATURE_FLAG_UPDATED = "feature_flag_updated", "Feature Flag Updated"
    DEVICE_INSTALLATION_REGISTERED = "device_installation_registered", "Device Installation Registered"
    DEVICE_INSTALLATION_REVOKED = "device_installation_revoked", "Device Installation Revoked"
    POLICY_ACCEPTED = "policy_accepted", "Policy Accepted"
    ACCOUNT_DELETION_REQUESTED = "account_deletion_requested", "Account Deletion Requested"
    ACCOUNT_DELETION_CANCELLED = "account_deletion_cancelled", "Account Deletion Cancelled"
    ACCOUNT_DELETION_COMPLETED = "account_deletion_completed", "Account Deletion Completed"
    NOTIFICATION_CAMPAIGN_CREATED = "notification_campaign_created", "Notification Campaign Created"


class AuditLog(BaseModel):
    actor = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, related_name="audit_logs", null=True, blank=True
    )
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
