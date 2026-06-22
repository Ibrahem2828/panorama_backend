from django.db import models


class UserRole(models.TextChoices):
    IT_SUPPORT = "it_support", "IT Support"
    ADMIN = "admin", "Admin"
    PRINT_STAFF = "print_staff", "Print Staff"
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
    RESET_PASSWORD = "reset_password", "Reset Password"
    LOGIN = "login", "Login"


class StudentAccountRequestStatus(models.TextChoices):
    PENDING_REVIEW = "pending_review", "Pending Review"
    APPROVED_PENDING_OTP = "approved_pending_otp", "Approved Pending OTP"
    OTP_SENT = "otp_sent", "OTP Sent"
    ACTIVE = "active", "Active"
    REJECTED = "rejected", "Rejected"
    NEEDS_UPDATE = "needs_update", "Needs Update"
    EXPIRED = "expired", "Expired"
