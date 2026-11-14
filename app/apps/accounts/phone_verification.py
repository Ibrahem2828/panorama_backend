"""Phone verification with a code a staff member sends by WhatsApp.

Flow: the student asks (phone + password) -> the request waits for staff (``pending_staff``) -> a staff member *claims* it,
which generates a 6-digit code on the server and shows it to that staff member once -> they send it by WhatsApp and press
"code sent" (``code_sent``, valid ``PHONE_VERIFICATION_CODE_TTL_MINUTES``) -> the student types it (``verified``).

The plain code lives only in the claim response. In the database there is a keyed HMAC (a bare hash of a 6-digit number
could be brute-forced from a leaked table). It is never logged and never audited.
"""

from __future__ import annotations

import hmac
import logging
import re
import secrets
from datetime import timedelta
from hashlib import sha256
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.responses import error_response

from .choices import PhoneVerificationStatus as Status
from .models import PhoneVerificationRequest, User

logger = logging.getLogger(__name__)

OPEN_STATUSES = (Status.PENDING_STAFF, Status.CODE_SENT)
#: Another staff member may take over a claimed request after this long.
CLAIM_HOLD = timedelta(minutes=15)
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_SEPARATORS = re.compile(r"[\s\-().\u200e\u200f]")
_CODE_RE = re.compile(r"\d{6}")


class PhoneVerificationRefused(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400, errors: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.errors = errors or {}


def refusal_response(refusal: PhoneVerificationRefused, request):
    return error_response(
        message=refusal.message,
        errors=refusal.errors,
        status_code=refusal.status_code,
        request=request,
        code=refusal.code,
    )


def _invalid_phone() -> PhoneVerificationRefused:
    return PhoneVerificationRefused(
        "PHONE_NUMBER_INVALID",
        "رقم الهاتف غير صالح. أدخل رقمًا سوريًا مثل 0912345678 أو رقمًا دوليًا يبدأ بـ +.",
        errors={"phone_number": "Invalid phone number."},
    )


def normalize_phone(raw: object) -> str:
    """E.164 with Syrian handling: ``0912345678``, ``963912345678``, ``00963 912 345 678``, ``+9630912345678``,
    Arabic-Indic digits and separators all become ``+963912345678``. Other countries need a leading ``+``/``00``."""

    value = _SEPARATORS.sub("", str(raw or "").translate(_ARABIC_DIGITS))
    if not value:
        raise _invalid_phone()
    if value.startswith("00"):
        value = "+" + value[2:]
    if value.startswith("+"):
        digits = value[1:]
    elif value.startswith("963"):
        digits = value
    elif value.startswith("0"):
        digits = "963" + value[1:]
    elif len(value) == 9 and value.startswith("9"):
        digits = "963" + value
    else:
        raise _invalid_phone()
    if not digits.isdigit() or digits.startswith("0"):
        raise _invalid_phone()
    if digits.startswith("963"):
        national = digits[3:].lstrip("0")  # +9630912... is a common typo for +963912...
        if not re.fullmatch(r"9\d{8}", national):
            raise _invalid_phone()
        return "+963" + national
    if not 8 <= len(digits) <= 15:
        raise _invalid_phone()
    return "+" + digits


def phone_variants(phone: str) -> set[str]:
    """Spellings an existing account may have stored for the same number."""

    variants = {phone}
    if phone.startswith("+963"):
        national = phone[4:]
        variants |= {"0" + national, "963" + national, "00963" + national, "+9630" + national}
    return variants


def hash_code(request_id: int, code: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode(), f"phone-verification:{request_id}:{code}".encode(), sha256
    ).hexdigest()


def generate_code() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def _phone_taken(phone: str, *, exclude_user_id: int) -> bool:
    return (
        User.objects.filter(phone_number__in=phone_variants(phone), is_deleted=False)
        .exclude(pk=exclude_user_id)
        .exists()
    )


def _in_use() -> PhoneVerificationRefused:
    return PhoneVerificationRefused(
        "PHONE_NUMBER_IN_USE",
        "هذا الرقم مرتبط بحساب آخر. استخدم رقمك الخاص أو تواصل مع الدعم.",
        status_code=409,
    )


def _notify(user, *, title: str, body: str, title_en: str, body_en: str, req: PhoneVerificationRequest) -> None:
    from apps.notifications.models import NotificationType
    from apps.notifications.services import NotificationService

    try:
        NotificationService.create_notification(
            user,
            title=title,
            body=body,
            type=NotificationType.VERIFICATION,
            data={"kind": "phone_verification", "request_id": req.pk, "status": req.status},
            title_ar=title,
            title_en=title_en,
            body_ar=body,
            body_en=body_en,
            deep_link="/setup/phone-verification",
        )
    except Exception:  # noqa: BLE001 - a notification must never undo a state change
        logger.exception("phone_verification_notification_failed", extra={"request_id": req.pk})


class PhoneVerificationService:
    # -- housekeeping ---------------------------------------------------------------------------------------------

    @classmethod
    def expire_stale(cls, *, user=None) -> int:
        """Close requests nobody finished: staff never handled them, or the code's 10 minutes passed."""

        now = timezone.now()
        stale = PhoneVerificationRequest.objects.filter(status__in=OPEN_STATUSES, is_deleted=False)
        if user is not None:
            stale = stale.filter(user=user)
        stale = stale.filter(
            models_q_stale(now, timedelta(hours=int(settings.PHONE_VERIFICATION_PENDING_TTL_HOURS)))
        ).select_related("user")[:200]
        count = 0
        for req in stale:
            with transaction.atomic():
                locked = PhoneVerificationRequest.objects.select_for_update().get(pk=req.pk)
                if locked.status not in OPEN_STATUSES:
                    continue
                was_sent = locked.status == Status.CODE_SENT
                locked.status = Status.EXPIRED
                locked.code_hash = ""
                locked.save(update_fields=["status", "code_hash", "updated_at"])
                AuditLogService.log(
                    actor=None,
                    action=AuditAction.PHONE_VERIFICATION_EXPIRED,
                    target=locked,
                    new_value={"code_was_sent": was_sent},
                )
            if was_sent:
                _notify(
                    req.user,
                    title="انتهت صلاحية رمز التحقق",
                    body="انتهت صلاحية رمز التحقق. ابدأ طلبًا جديدًا للحصول على رمز آخر.",
                    title_en="Your verification code expired",
                    body_en="The verification code expired. Start a new request to get another one.",
                    req=locked,
                )
            count += 1
        return count

    @staticmethod
    def open_request(user) -> PhoneVerificationRequest | None:
        return PhoneVerificationRequest.objects.filter(user=user, status__in=OPEN_STATUSES, is_deleted=False).first()

    # -- student --------------------------------------------------------------------------------------------------

    @classmethod
    def create(cls, user, phone_raw: str, password: str, request=None) -> PhoneVerificationRequest:
        if not user.check_password(password):
            raise PhoneVerificationRefused(
                "PASSWORD_INCORRECT", "كلمة المرور غير صحيحة.", errors={"password": "Incorrect password."}
            )
        phone = normalize_phone(phone_raw)
        if user.is_phone_verified and user.phone_number in phone_variants(phone):
            raise PhoneVerificationRefused("PHONE_ALREADY_VERIFIED", "رقم هاتفك موثّق بالفعل.", status_code=409)
        if _phone_taken(phone, exclude_user_id=user.pk):
            raise _in_use()
        cls.expire_stale(user=user)
        already_open = PhoneVerificationRefused(
            "PHONE_VERIFICATION_ALREADY_OPEN",
            "لديك طلب توثيق قائم. ألغِه أولًا إن أردت تغيير الرقم.",
            status_code=409,
        )
        if cls.open_request(user) is not None:
            raise already_open
        try:
            with transaction.atomic():
                req = PhoneVerificationRequest.objects.create(user=user, phone_number=phone)
        except IntegrityError:
            raise already_open from None  # concurrent duplicate: the partial unique constraint caught it
        AuditLogService.log(
            actor=user,
            action=AuditAction.PHONE_VERIFICATION_REQUESTED,
            target=req,
            new_value={"request_id": req.pk},
            request=request,
        )
        from apps.notifications.staff import notify_staff

        notify_staff(
            "phone_verification_request",
            "طلب توثيق رقم هاتف جديد",
            f"{user.full_name} يطلب توثيق رقم هاتفه عبر واتساب.",
            object_id=req.pk,
            actor_id=user.pk,
            extra={"request_id": req.pk},
        )
        return req

    @classmethod
    def current(cls, user) -> PhoneVerificationRequest | None:
        cls.expire_stale(user=user)
        return (
            PhoneVerificationRequest.objects.filter(user=user, is_deleted=False).order_by("-created_at", "-id").first()
        )

    @classmethod
    def cancel(cls, user, request=None) -> PhoneVerificationRequest:
        cls.expire_stale(user=user)
        with transaction.atomic():
            req = (
                PhoneVerificationRequest.objects.select_for_update()
                .filter(user=user, status__in=OPEN_STATUSES, is_deleted=False)
                .first()
            )
            if req is None:
                raise PhoneVerificationRefused(
                    "PHONE_VERIFICATION_NOT_FOUND", "لا يوجد طلب توثيق قائم.", status_code=404
                )
            req.status = Status.CANCELLED
            req.code_hash = ""
            req.save(update_fields=["status", "code_hash", "updated_at"])
            AuditLogService.log(
                actor=user, action=AuditAction.PHONE_VERIFICATION_CANCELLED, target=req, request=request
            )
        return req

    @classmethod
    def confirm(cls, user, code: str, request=None) -> PhoneVerificationRequest:
        """Check the typed code. The attempt counter is committed even when the code is wrong (the refusal is raised
        after the transaction ends), so it cannot be rolled back to retry."""

        cls.expire_stale(user=user)
        failure: PhoneVerificationRefused | None = None
        audit_failed = False
        with transaction.atomic():
            req = (
                PhoneVerificationRequest.objects.select_for_update()
                .filter(user=user, status__in=OPEN_STATUSES, is_deleted=False)
                .first()
            )
            if req is None:
                raise PhoneVerificationRefused(
                    "PHONE_VERIFICATION_NOT_FOUND", "لا يوجد طلب توثيق قائم.", status_code=404
                )
            if req.status != Status.CODE_SENT:
                raise PhoneVerificationRefused(
                    "PHONE_CODE_NOT_SENT", "لم يُرسل رمز التحقق بعد. انتظر إشعار الإرسال.", status_code=409
                )
            max_attempts = int(settings.PHONE_VERIFICATION_MAX_ATTEMPTS)
            if req.attempts >= max_attempts or (req.expires_at and req.expires_at <= timezone.now()):
                req.status = Status.EXPIRED
                req.code_hash = ""
                req.save(update_fields=["status", "code_hash", "updated_at"])
                failure = PhoneVerificationRefused(
                    "PHONE_CODE_EXPIRED", "انتهت صلاحية الرمز. ابدأ طلبًا جديدًا.", status_code=400
                )
            else:
                req.attempts += 1
                matches = hmac.compare_digest(hash_code(req.pk, code), req.code_hash)
                if matches:
                    failure = cls._complete(req, user, request)
                else:
                    audit_failed = True
                    remaining = max(0, max_attempts - req.attempts)
                    if remaining == 0:
                        req.status = Status.EXPIRED
                        req.code_hash = ""
                    req.save(update_fields=["attempts", "status", "code_hash", "updated_at"])
                    failure = PhoneVerificationRefused(
                        "PHONE_CODE_ATTEMPTS_EXCEEDED" if remaining == 0 else "PHONE_CODE_INVALID",
                        "تجاوزت عدد المحاولات. ابدأ طلبًا جديدًا." if remaining == 0 else "الرمز غير صحيح.",
                        errors={"code": "Invalid code.", "attempts_remaining": remaining},
                    )
        if audit_failed:
            AuditLogService.log(
                actor=user,
                action=AuditAction.PHONE_VERIFICATION_FAILED,
                target=req,
                new_value={"attempts": req.attempts},
                request=request,
            )
        if failure is not None:
            raise failure
        return req

    @classmethod
    def _complete(cls, req: PhoneVerificationRequest, user, request) -> PhoneVerificationRefused | None:
        """Right code: take the number. Runs inside ``confirm``'s transaction; returns a refusal (committed state) or None."""

        locked_user = User.objects.select_for_update().get(pk=user.pk)
        conflict = _phone_taken(req.phone_number, exclude_user_id=locked_user.pk)
        if not conflict:
            try:
                with transaction.atomic():
                    locked_user.phone_number = req.phone_number
                    locked_user.is_phone_verified = True
                    locked_user.save(update_fields=["phone_number", "is_phone_verified", "updated_at"])
            except IntegrityError:
                conflict = True
        now = timezone.now()
        if conflict:
            req.status = Status.REJECTED
            req.rejection_reason = "phone_number_in_use"
            req.code_hash = ""
            req.save()
            return _in_use()
        req.status = Status.VERIFIED
        req.verified_at = now
        req.code_hash = ""
        req.save()
        user.phone_number = locked_user.phone_number
        user.is_phone_verified = True
        AuditLogService.log(
            actor=user, action=AuditAction.PHONE_VERIFICATION_VERIFIED, target=req, request=request, critical=True
        )
        _notify(
            user,
            title="تم توثيق رقم هاتفك",
            body="تم ربط رقم هاتفك بحسابك وتوثيقه.",
            title_en="Your phone number is verified",
            body_en="Your phone number is now linked to your account and verified.",
            req=req,
        )
        return None

    # -- staff ----------------------------------------------------------------------------------------------------

    @classmethod
    def claim(cls, req_id: int, staff, request=None) -> tuple[PhoneVerificationRequest, str]:
        """Open a request and generate its code. The code is returned once; claiming again by the same staff member
        issues a fresh code (the previous one stops working)."""

        cls.expire_stale()
        with transaction.atomic():
            req = cls._locked(req_id)
            if req.status != Status.PENDING_STAFF:
                raise PhoneVerificationRefused(
                    "PHONE_VERIFICATION_NOT_PENDING",
                    "هذا الطلب لم يعد بانتظار الإرسال.",
                    status_code=409,
                    errors={"status": req.status},
                )
            now = timezone.now()
            if (
                req.claimed_by_id
                and req.claimed_by_id != staff.pk
                and req.claimed_at
                and req.claimed_at > now - CLAIM_HOLD
            ):
                raise PhoneVerificationRefused(
                    "PHONE_VERIFICATION_CLAIMED_BY_OTHER",
                    "موظف آخر يعالج هذا الطلب حاليًا.",
                    status_code=409,
                )
            if _phone_taken(req.phone_number, exclude_user_id=req.user_id):
                raise _in_use()
            code = generate_code()
            req.code_hash = hash_code(req.pk, code)
            req.code_generated_at = now
            req.claimed_by = staff
            req.claimed_at = now
            req.save(update_fields=["code_hash", "code_generated_at", "claimed_by", "claimed_at", "updated_at"])
            AuditLogService.log(
                actor=staff,
                action=AuditAction.PHONE_VERIFICATION_CODE_ISSUED,
                target=req,
                new_value={"request_id": req.pk},  # never the code
                request=request,
            )
        return req, code

    @classmethod
    def mark_sent(cls, req_id: int, staff, request=None) -> PhoneVerificationRequest:
        cls.expire_stale()
        with transaction.atomic():
            req = cls._locked(req_id)
            if req.status != Status.PENDING_STAFF or not req.code_hash:
                raise PhoneVerificationRefused(
                    "PHONE_VERIFICATION_NOT_CLAIMED",
                    "افتح الطلب أولًا لتوليد الرمز ثم أرسله.",
                    status_code=409,
                    errors={"status": req.status},
                )
            if req.claimed_by_id != staff.pk:
                raise PhoneVerificationRefused(
                    "PHONE_VERIFICATION_CLAIMED_BY_OTHER", "هذا الطلب بعهدة موظف آخر.", status_code=409
                )
            now = timezone.now()
            req.status = Status.CODE_SENT
            req.code_sent_at = now
            req.expires_at = now + timedelta(minutes=int(settings.PHONE_VERIFICATION_CODE_TTL_MINUTES))
            req.sent_by = staff
            req.save(update_fields=["status", "code_sent_at", "expires_at", "sent_by", "updated_at"])
            AuditLogService.log(
                actor=staff,
                action=AuditAction.PHONE_VERIFICATION_CODE_SENT,
                target=req,
                new_value={"expires_at": req.expires_at.isoformat()},
                request=request,
            )
        minutes = int(settings.PHONE_VERIFICATION_CODE_TTL_MINUTES)
        _notify(
            req.user,
            title="أُرسل إليك رمز التحقق",
            body=f"أرسلنا رمز التحقق إلى واتساب الخاص بك. أدخله خلال {minutes} دقائق.",
            title_en="Your verification code was sent",
            body_en=f"We sent the verification code to your WhatsApp. Enter it within {minutes} minutes.",
            req=req,
        )
        return req

    @classmethod
    def reject(cls, req_id: int, staff, reason: str, request=None) -> PhoneVerificationRequest:
        reason = (reason or "").strip()
        if not reason:
            raise PhoneVerificationRefused(
                "REASON_REQUIRED", "سبب الرفض مطلوب.", errors={"reason": "A reason is required."}
            )
        with transaction.atomic():
            req = cls._locked(req_id)
            if req.status not in OPEN_STATUSES:
                raise PhoneVerificationRefused(
                    "PHONE_VERIFICATION_NOT_OPEN", "هذا الطلب مغلق.", status_code=409, errors={"status": req.status}
                )
            req.status = Status.REJECTED
            req.rejection_reason = reason[:255]
            req.decided_by = staff
            req.code_hash = ""
            req.save(update_fields=["status", "rejection_reason", "decided_by", "code_hash", "updated_at"])
            AuditLogService.log(
                actor=staff,
                action=AuditAction.PHONE_VERIFICATION_REJECTED,
                target=req,
                new_value={"reason": reason[:255]},
                request=request,
            )
        _notify(
            req.user,
            title="تعذّر توثيق رقم هاتفك",
            body=reason[:255],
            title_en="We could not verify your phone number",
            body_en=reason[:255],
            req=req,
        )
        return req

    @staticmethod
    def _locked(req_id: int) -> PhoneVerificationRequest:
        req = (
            PhoneVerificationRequest.objects.select_for_update()
            .select_related("user")
            .filter(pk=req_id, is_deleted=False)
            .first()
        )
        if req is None:
            raise PhoneVerificationRefused("PHONE_VERIFICATION_NOT_FOUND", "الطلب غير موجود.", status_code=404)
        return req


def models_q_stale(now, pending_ttl: timedelta):
    """Open requests that can no longer succeed: unhandled for too long, or a code past its expiry."""

    from django.db.models import Q

    return Q(status=Status.PENDING_STAFF, created_at__lt=now - pending_ttl) | Q(
        status=Status.CODE_SENT, expires_at__lte=now
    )
