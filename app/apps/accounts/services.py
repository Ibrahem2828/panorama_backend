from django.conf import settings
from rest_framework.exceptions import ValidationError

from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService

from .choices import OTPPurpose
from .models import OTPCode, User
from .phone_numbers import normalize_phone_number


class OTPService:
    @staticmethod
    def send_otp(phone_number: str, purpose: str, user: User | None = None) -> tuple[OTPCode, str | None]:
        if purpose not in OTPPurpose.values:
            raise ValidationError({"purpose": "Invalid OTP purpose."})

        phone_number = normalize_phone_number(phone_number)
        raw_code = OTPCode.generate_code()
        otp = OTPCode(
            user=user,
            phone_number=phone_number,
            purpose=purpose,
            expires_at=OTPCode.default_expiry(),
        )
        otp.set_code(raw_code)
        otp.save()

        if settings.RETURN_DEVELOPMENT_OTP:
            return otp, raw_code
        return otp, None

    @staticmethod
    def verify_otp(phone_number: str, code: str, purpose: str) -> OTPCode:
        if purpose not in OTPPurpose.values:
            raise ValidationError({"purpose": "Invalid OTP purpose."})

        otp = (
            OTPCode.objects.filter(
                phone_number=normalize_phone_number(phone_number),
                purpose=purpose,
                is_used=False,
            )
            .order_by("-created_at")
            .first()
        )
        if otp is None:
            raise ValidationError({"code": "Invalid or expired OTP code."})
        if otp.is_expired():
            raise ValidationError({"code": "OTP code has expired."})
        if otp.attempts_count >= settings.MAX_OTP_VERIFY_ATTEMPTS:
            AuditLogService.log(
                actor=otp.user,
                action=AuditAction.OTP_VERIFICATION_FAILED,
                target=otp.user,
                new_value={"purpose": purpose, "reason": "max_attempts"},
            )
            raise ValidationError({"code": "Maximum OTP verification attempts exceeded."})
        if not otp.verify_code(code):
            otp.refresh_from_db(fields=["attempts_count"])
            if otp.attempts_count >= settings.MAX_OTP_VERIFY_ATTEMPTS:
                AuditLogService.log(
                    actor=otp.user,
                    action=AuditAction.OTP_VERIFICATION_FAILED,
                    target=otp.user,
                    new_value={"purpose": purpose, "reason": "max_attempts"},
                )
            raise ValidationError({"code": "Invalid OTP code."})
        otp.mark_used()
        return otp
