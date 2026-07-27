from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError

from .choices import OTPDeliveryChannel, OTPPurpose
from .models import OTPCode, User

logger = logging.getLogger(__name__)


class OTPDeliveryUnavailable(APIException):
    status_code = 503
    default_detail = "OTP delivery is temporarily unavailable."
    default_code = "otp_delivery_unavailable"


class OTPService:
    @staticmethod
    def _normalise_identifier(identifier: str, channel: str) -> str:
        value = str(identifier or "").strip()
        if channel == OTPDeliveryChannel.EMAIL:
            return value.lower()
        return value

    @staticmethod
    def _lookup_filter(identifier: str, channel: str) -> dict:
        return {"email": identifier} if channel == OTPDeliveryChannel.EMAIL else {"phone_number": identifier}

    @staticmethod
    def _deliver_email(identifier: str, raw_code: str, purpose: str) -> None:
        if not settings.EMAIL_HOST_PASSWORD and "smtp" in settings.EMAIL_BACKEND:
            raise OTPDeliveryUnavailable("SMTP credentials are not configured.")
        purpose_labels = {
            OTPPurpose.VERIFY_EMAIL: "تأكيد البريد الإلكتروني",
            OTPPurpose.RESET_PASSWORD: "إعادة تعيين كلمة المرور",
            OTPPurpose.LOGIN: "تسجيل الدخول",
            OTPPurpose.REGISTER: "إنشاء الحساب",
            OTPPurpose.VERIFY_PHONE: "تأكيد الحساب",
        }
        purpose_label = purpose_labels.get(purpose, "تأكيد الحساب")
        body = (
            "مرحبًا بك في بانوراما،\n\n"
            f"رمز التحقق الخاص بعملية {purpose_label} هو: {raw_code}\n"
            f"صلاحية الرمز {settings.OTP_EXPIRY_MINUTES} دقائق.\n\n"
            "لا تشارك هذا الرمز مع أي شخص. فريق بانوراما لن يطلبه منك.\n"
            f"للدعم: {settings.SUPPORT_EMAIL}"
        )
        send_mail(
            subject=settings.OTP_EMAIL_SUBJECT,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[identifier],
            fail_silently=False,
        )

    @staticmethod
    def _deliver_phone(identifier: str, raw_code: str, purpose: str) -> None:
        # Provider adapter intentionally fails closed in production until a real
        # SMS/WhatsApp provider is connected through a secrets-managed service.
        if settings.DEBUG and settings.RETURN_DEVELOPMENT_OTP:
            logger.info("Development phone OTP for %s (%s): %s", identifier, purpose, raw_code)
            return
        if not settings.SMS_OTP_PROVIDER_ENABLED:
            raise OTPDeliveryUnavailable("Phone OTP provider is not configured. Use email verification.")
        raise OTPDeliveryUnavailable("Phone OTP provider adapter has not been implemented.")

    @classmethod
    @transaction.atomic
    def send_otp(
        cls,
        identifier: str,
        purpose: str,
        user: User | None = None,
        channel: str | None = None,
    ) -> tuple[OTPCode, str | None]:
        if purpose not in OTPPurpose.values:
            raise ValidationError({"purpose": "Invalid OTP purpose."})
        channel = channel or (OTPDeliveryChannel.EMAIL if "@" in str(identifier) else OTPDeliveryChannel.PHONE)
        if channel not in OTPDeliveryChannel.values:
            raise ValidationError({"channel": "Invalid OTP delivery channel."})

        identifier = cls._normalise_identifier(identifier, channel)
        if not identifier:
            raise ValidationError({"identifier": "Email or phone number is required."})

        active_filter = cls._lookup_filter(identifier, channel)
        previous = (
            OTPCode.objects.select_for_update()
            .filter(**active_filter, purpose=purpose, delivery_channel=channel, is_used=False)
            .order_by("-created_at")
            .first()
        )
        if previous:
            cooldown_until = previous.created_at + timedelta(seconds=settings.OTP_RESEND_COOLDOWN_SECONDS)
            if timezone.now() < cooldown_until and not previous.is_expired():
                seconds = max(1, int((cooldown_until - timezone.now()).total_seconds()))
                raise ValidationError({"identifier": f"Please wait {seconds} seconds before requesting another code."})
            OTPCode.objects.filter(**active_filter, purpose=purpose, delivery_channel=channel, is_used=False).update(
                is_used=True,
                updated_at=timezone.now(),
            )

        raw_code = OTPCode.generate_code()
        otp = OTPCode(
            user=user,
            purpose=purpose,
            delivery_channel=channel,
            expires_at=OTPCode.default_expiry(),
            email=identifier if channel == OTPDeliveryChannel.EMAIL else "",
            phone_number=identifier if channel == OTPDeliveryChannel.PHONE else "",
        )
        otp.set_code(raw_code)
        otp.save()

        try:
            if channel == OTPDeliveryChannel.EMAIL:
                cls._deliver_email(identifier, raw_code, purpose)
            else:
                cls._deliver_phone(identifier, raw_code, purpose)
        except Exception:
            otp.is_used = True
            otp.save(update_fields=["is_used", "updated_at"])
            raise

        development_code = raw_code if settings.RETURN_DEVELOPMENT_OTP else None
        return otp, development_code

    @classmethod
    @transaction.atomic
    def verify_otp(cls, identifier: str, code: str, purpose: str, channel: str | None = None) -> OTPCode:
        if purpose not in OTPPurpose.values:
            raise ValidationError({"purpose": "Invalid OTP purpose."})
        channel = channel or (OTPDeliveryChannel.EMAIL if "@" in str(identifier) else OTPDeliveryChannel.PHONE)
        identifier = cls._normalise_identifier(identifier, channel)
        lookup = cls._lookup_filter(identifier, channel)

        otp = (
            OTPCode.objects.select_for_update()
            .filter(**lookup, purpose=purpose, delivery_channel=channel, is_used=False)
            .order_by("-created_at")
            .first()
        )
        if otp is None or otp.is_expired():
            raise ValidationError({"code": "Invalid or expired OTP code."})
        if otp.is_locked():
            raise ValidationError({"code": "Too many invalid attempts. Request a new code."})
        if not otp.verify_code(code):
            if otp.is_locked():
                raise ValidationError({"code": "Too many invalid attempts. Request a new code."})
            raise ValidationError({"code": "Invalid or expired OTP code."})
        otp.mark_used()
        return otp
