import logging

from django.conf import settings
from rest_framework.exceptions import ValidationError

from .choices import OTPPurpose
from .models import OTPCode, User

logger = logging.getLogger(__name__)


class OTPService:
    @staticmethod
    def send_otp(phone_number: str, purpose: str, user: User | None = None) -> tuple[OTPCode, str | None]:
        if purpose not in OTPPurpose.values:
            raise ValidationError({"purpose": "Invalid OTP purpose."})

        phone_number = phone_number.strip()
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
            logger.info("Development OTP for %s (%s): %s", phone_number, purpose, raw_code)
            return otp, raw_code
        return otp, None

    @staticmethod
    def verify_otp(phone_number: str, code: str, purpose: str) -> OTPCode:
        if purpose not in OTPPurpose.values:
            raise ValidationError({"purpose": "Invalid OTP purpose."})

        otp = (
            OTPCode.objects.filter(
                phone_number=phone_number.strip(),
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
        if not otp.verify_code(code):
            raise ValidationError({"code": "Invalid OTP code."})
        otp.mark_used()
        return otp
