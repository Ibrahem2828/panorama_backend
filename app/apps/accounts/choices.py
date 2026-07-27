from django.db import models


class UserRole(models.TextChoices):
    IT_SUPPORT = "it_support", "IT Support"
    ADMIN = "admin", "Admin"
    PRINT_STAFF = "print_staff", "Print Staff"
    SUPPORT_STAFF = "support_staff", "Support Staff"
    CONTENT_MANAGER = "content_manager", "Content Manager"
    STUDENT = "student", "Student"
    NORMAL_USER = "normal_user", "Normal User"


class StudentVerificationStatus(models.TextChoices):
    INCOMPLETE = "incomplete", "Incomplete"
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    NEEDS_UPDATE = "needs_update", "Needs Update"
    SUSPENDED = "suspended", "Suspended"


class OTPPurpose(models.TextChoices):
    REGISTER = "register", "Register"
    VERIFY_PHONE = "verify_phone", "Verify Phone"
    VERIFY_EMAIL = "verify_email", "Verify Email"
    RESET_PASSWORD = "reset_password", "Reset Password"
    LOGIN = "login", "Login"


class OTPDeliveryChannel(models.TextChoices):
    EMAIL = "email", "Email"
    PHONE = "phone", "Phone"


class PermissionEffect(models.TextChoices):
    ALLOW = "allow", "Allow"
    DENY = "deny", "Deny"
