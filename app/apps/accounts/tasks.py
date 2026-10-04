from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from apps.common.crypto import decrypt_text

from .models import OTPCode
from .services import OTPService

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(OSError,),  # smtplib.SMTPException and socket timeouts are OSError subclasses
    retry_backoff=2,
    retry_backoff_max=30,
    retry_jitter=True,
    max_retries=4,
    ignore_result=True,
)
def send_otp_email(self, otp_id: int, encrypted_code: str) -> None:
    """Deliver an OTP email outside the request cycle.

    The raw code travels encrypted so it never sits in the broker in clear text. A code that was
    already used, expired or replaced by a newer request is dropped instead of being sent late.
    """

    otp = OTPCode.objects.filter(pk=otp_id).first()
    if otp is None or otp.is_used or otp.expires_at <= timezone.now():
        logger.info("otp_email_skipped", extra={"otp_id": otp_id})
        return
    OTPService._deliver_email(otp.email, decrypt_text(encrypted_code), otp.purpose)
    logger.info("otp_email_sent", extra={"otp_id": otp_id, "attempt": self.request.retries + 1})
