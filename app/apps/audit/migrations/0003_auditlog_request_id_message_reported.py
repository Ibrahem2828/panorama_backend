from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("audit", "0002_alter_auditlog_action"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditlog",
            name="request_id",
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AlterField(
            model_name="auditlog",
            name="action",
            field=models.CharField(
                choices=[
                    ("user_registered", "User Registered"),
                    ("user_login_succeeded", "User Login Succeeded"),
                    ("user_login_failed", "User Login Failed"),
                    ("user_logged_out", "User Logged Out"),
                    ("password_changed", "Password Changed"),
                    ("password_reset_confirmed", "Password Reset Confirmed"),
                    ("otp_verification_failed", "OTP Verification Failed"),
                    ("student_profile_updated", "Student Profile Updated"),
                    ("verification_submitted", "Verification Submitted"),
                    ("verification_approved", "Verification Approved"),
                    ("verification_rejected", "Verification Rejected"),
                    ("verification_needs_update", "Verification Needs Update"),
                    ("group_created", "Group Created"),
                    ("group_updated", "Group Updated"),
                    ("group_deleted", "Group Deleted"),
                    ("group_membership_approved", "Group Membership Approved"),
                    ("group_membership_rejected", "Group Membership Rejected"),
                    ("group_membership_blocked", "Group Membership Blocked"),
                    ("group_membership_role_changed", "Group Membership Role Changed"),
                    ("file_uploaded", "File Uploaded"),
                    ("file_updated", "File Updated"),
                    ("file_deleted", "File Deleted"),
                    ("file_accessed", "File Accessed"),
                    ("announcement_created", "Announcement Created"),
                    ("announcement_updated", "Announcement Updated"),
                    ("announcement_deleted", "Announcement Deleted"),
                    ("print_order_created", "Print Order Created"),
                    ("print_order_assigned", "Print Order Assigned"),
                    ("print_order_status_changed", "Print Order Status Changed"),
                    ("print_order_note_updated", "Print Order Note Updated"),
                    ("support_ticket_created", "Support Ticket Created"),
                    ("support_ticket_assigned", "Support Ticket Assigned"),
                    ("support_ticket_status_changed", "Support Ticket Status Changed"),
                    ("support_ticket_priority_changed", "Support Ticket Priority Changed"),
                    ("support_ticket_staff_reply", "Support Ticket Staff Reply"),
                    ("message_deleted", "Message Deleted"),
                    ("message_reported", "Message Reported"),
                ],
                max_length=64,
            ),
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(fields=["request_id"], name="audit_audit_request_06fe30_idx"),
        ),
    ]
