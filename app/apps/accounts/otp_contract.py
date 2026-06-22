from django.conf import settings

from .choices import OTPPurpose, UserRole
from .models import OTPCode


OTP_EXPIRY_SECONDS = OTPCode.DEFAULT_EXPIRY_MINUTES * 60


def get_resend_cooldown_seconds() -> int:
    return int(getattr(settings, "STUDENT_ACCOUNT_OTP_RESEND_COOLDOWN_SECONDS", 60))


def requires_phone_verification_for_user(user) -> bool:
    return user.role == UserRole.NORMAL_USER and not user.is_phone_verified


def mask_phone_number(phone_number: str) -> str:
    phone_number = (phone_number or "").strip()
    if len(phone_number) <= 4:
        return phone_number
    return f"{phone_number[:4]}{'*' * max(len(phone_number) - 7, 0)}{phone_number[-3:]}"


def build_phone_otp_register_payload(user) -> dict:
    return {
        "requires_otp": True,
        "otp_purpose": OTPPurpose.VERIFY_PHONE,
        "phone_number": user.phone_number,
        "phone_number_masked": mask_phone_number(user.phone_number),
        "phone_verified": user.is_phone_verified,
        "requires_phone_verification": requires_phone_verification_for_user(user),
        "next_step": "verify_phone",
        "expires_in_seconds": OTP_EXPIRY_SECONDS,
        "resend_after_seconds": get_resend_cooldown_seconds(),
    }


def build_phone_otp_send_payload(*, purpose: str, phone_number: str, expires_at=None) -> dict:
    payload = {
        "otp_purpose": purpose,
        "phone_number": phone_number,
        "phone_number_masked": mask_phone_number(phone_number),
        "expires_in_seconds": OTP_EXPIRY_SECONDS,
        "resend_after_seconds": get_resend_cooldown_seconds(),
        "next_step": "verify_phone" if purpose == OTPPurpose.VERIFY_PHONE else "verify_otp",
    }
    if expires_at is not None:
        payload["expires_at"] = expires_at
    return payload


def build_verify_phone_success_payload() -> dict:
    return {
        "phone_verified": True,
        "requires_phone_verification": False,
        "is_phone_verified": True,
        "next_step": "login",
    }