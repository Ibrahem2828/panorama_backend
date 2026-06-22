from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.accounts.choices import StudentAccountRequestStatus, StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.accounts.student_account_request_models import StudentAccountRequest
from apps.accounts.otp_contract import get_resend_cooldown_seconds
from apps.accounts.student_number import apply_student_number_parse
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.protected_media import ProtectedMediaService
from apps.notifications.models import NotificationType
from apps.notifications.services import NotificationService


class StudentAccountRequestService:
    @staticmethod
    def get_request_or_404(public_id) -> StudentAccountRequest:
        request_obj = StudentAccountRequest.objects.filter(public_id=public_id, is_deleted=False).first()
        if request_obj is None:
            raise ValidationError({"request_id": "Student account request not found."})
        return request_obj

    @staticmethod
    def get_dashboard_request_or_404(pk: int) -> StudentAccountRequest:
        request_obj = StudentAccountRequest.objects.filter(pk=pk, is_deleted=False).first()
        if request_obj is None:
            raise ValidationError({"id": "Student account request not found."})
        return request_obj

    @staticmethod
    def build_manual_whatsapp_message(phone_number: str, otp_code: str) -> str:
        return f"رمز تفعيل حسابك في بانوراما هو: {otp_code}. الرمز صالح لمدة 10 دقائق."

    @staticmethod
    def build_dashboard_otp_payload(request_obj: StudentAccountRequest, raw_otp: str) -> dict:
        return {
            "request_id": str(request_obj.public_id),
            "status": request_obj.status,
            "otp_code": raw_otp,
            "otp_expires_at": request_obj.otp_expires_at,
            "resend_after_seconds": get_resend_cooldown_seconds(),
            "whatsapp_phone": request_obj.phone_number,
            "manual_whatsapp_message": StudentAccountRequestService.build_manual_whatsapp_message(
                request_obj.phone_number,
                raw_otp,
            ),
        }

    @staticmethod
    def _generate_and_store_otp(request_obj: StudentAccountRequest) -> str:
        raw_otp = StudentAccountRequest.generate_otp_code()
        request_obj.set_otp(raw_otp)
        if request_obj.status == StudentAccountRequestStatus.APPROVED_PENDING_OTP:
            request_obj.status = StudentAccountRequestStatus.OTP_SENT
        return raw_otp

    @staticmethod
    @transaction.atomic
    def approve(request_obj: StudentAccountRequest, reviewer, admin_note: str = "", request=None) -> tuple[StudentAccountRequest, str]:
        request_obj = (
            StudentAccountRequest.objects.select_for_update()
            .select_related("university", "faculty", "major", "reviewed_by")
            .get(pk=request_obj.pk, is_deleted=False)
        )
        if request_obj.status not in {
            StudentAccountRequestStatus.PENDING_REVIEW,
            StudentAccountRequestStatus.NEEDS_UPDATE,
        }:
            raise ValidationError("Only pending or needs-update requests can be approved.")

        now = timezone.now()
        request_obj.status = StudentAccountRequestStatus.APPROVED_PENDING_OTP
        request_obj.admin_note = admin_note
        request_obj.reviewed_by = reviewer
        request_obj.reviewed_at = now
        request_obj.approved_at = now
        request_obj.rejection_reason = ""
        request_obj.needs_update_reason = ""
        raw_otp = StudentAccountRequestService._generate_and_store_otp(request_obj)
        request_obj.save()

        AuditLogService.log(
            actor=reviewer,
            action=AuditAction.STUDENT_ACCOUNT_REQUEST_APPROVED,
            target=request_obj,
            new_value={"status": request_obj.status, "request_id": str(request_obj.public_id)},
            request=request,
        )
        AuditLogService.log(
            actor=reviewer,
            action=AuditAction.STUDENT_ACCOUNT_OTP_GENERATED,
            target=request_obj,
            new_value={"status": request_obj.status, "request_id": str(request_obj.public_id)},
            request=request,
        )
        return request_obj, raw_otp

    @staticmethod
    @transaction.atomic
    def reject(request_obj: StudentAccountRequest, reviewer, rejection_reason: str, admin_note: str = "", request=None) -> StudentAccountRequest:
        request_obj = StudentAccountRequest.objects.select_for_update().get(pk=request_obj.pk, is_deleted=False)
        if request_obj.status != StudentAccountRequestStatus.PENDING_REVIEW:
            raise ValidationError("Only pending review requests can be rejected.")

        now = timezone.now()
        request_obj.status = StudentAccountRequestStatus.REJECTED
        request_obj.rejection_reason = rejection_reason
        request_obj.admin_note = admin_note
        request_obj.reviewed_by = reviewer
        request_obj.reviewed_at = now
        request_obj.clear_otp()
        request_obj.save()

        AuditLogService.log(
            actor=reviewer,
            action=AuditAction.STUDENT_ACCOUNT_REQUEST_REJECTED,
            target=request_obj,
            new_value={"status": request_obj.status, "request_id": str(request_obj.public_id)},
            request=request,
        )
        return request_obj

    @staticmethod
    @transaction.atomic
    def needs_update(
        request_obj: StudentAccountRequest,
        reviewer,
        needs_update_reason: str,
        admin_note: str = "",
        request=None,
    ) -> StudentAccountRequest:
        request_obj = StudentAccountRequest.objects.select_for_update().get(pk=request_obj.pk, is_deleted=False)
        if request_obj.status != StudentAccountRequestStatus.PENDING_REVIEW:
            raise ValidationError("Only pending review requests can be marked as needs update.")

        now = timezone.now()
        request_obj.status = StudentAccountRequestStatus.NEEDS_UPDATE
        request_obj.needs_update_reason = needs_update_reason
        request_obj.admin_note = admin_note
        request_obj.reviewed_by = reviewer
        request_obj.reviewed_at = now
        request_obj.clear_otp()
        request_obj.save()

        AuditLogService.log(
            actor=reviewer,
            action=AuditAction.STUDENT_ACCOUNT_REQUEST_NEEDS_UPDATE,
            target=request_obj,
            new_value={"status": request_obj.status, "request_id": str(request_obj.public_id)},
            request=request,
        )
        return request_obj

    @staticmethod
    @transaction.atomic
    def resend_otp(request_obj: StudentAccountRequest, reviewer, request=None) -> tuple[StudentAccountRequest, str]:
        request_obj = StudentAccountRequest.objects.select_for_update().get(pk=request_obj.pk, is_deleted=False)
        if request_obj.status not in StudentAccountRequest.otp_eligible_statuses():
            raise ValidationError("OTP can only be resent for approved requests.")

        if request_obj.otp_resend_count > 0 and request_obj.otp_last_sent_at:
            elapsed = (timezone.now() - request_obj.otp_last_sent_at).total_seconds()
            cooldown = settings.STUDENT_ACCOUNT_OTP_RESEND_COOLDOWN_SECONDS
            if elapsed < cooldown:
                remaining = int(cooldown - elapsed)
                raise ValidationError(f"Please wait {remaining} seconds before resending OTP.")

        raw_otp = StudentAccountRequestService._generate_and_store_otp(request_obj)
        request_obj.otp_resend_count += 1
        request_obj.save()

        AuditLogService.log(
            actor=reviewer,
            action=AuditAction.STUDENT_ACCOUNT_OTP_RESENT,
            target=request_obj,
            new_value={"status": request_obj.status, "request_id": str(request_obj.public_id)},
            request=request,
        )
        return request_obj, raw_otp

    @staticmethod
    def verify_otp(request_obj: StudentAccountRequest, code: str, request=None) -> StudentAccountRequest:
        request_obj = StudentAccountRequest.objects.select_related(
            "university",
            "faculty",
            "major",
            "reviewed_by",
        ).get(pk=request_obj.pk, is_deleted=False)
        if request_obj.status not in StudentAccountRequest.otp_eligible_statuses():
            raise ValidationError({"code": "OTP verification is not available for this request."})
        if request_obj.is_otp_expired():
            raise ValidationError({"code": "OTP code has expired."})
        if request_obj.otp_attempt_count >= settings.MAX_OTP_VERIFY_ATTEMPTS:
            AuditLogService.log(
                action=AuditAction.STUDENT_ACCOUNT_OTP_FAILED,
                target=request_obj,
                new_value={"reason": "max_attempts", "request_id": str(request_obj.public_id)},
                request=request,
            )
            raise ValidationError({"code": "Maximum OTP verification attempts exceeded."})
        if not request_obj.verify_otp_code(code):
            request_obj.refresh_from_db(fields=["otp_attempt_count"])
            if request_obj.otp_attempt_count >= settings.MAX_OTP_VERIFY_ATTEMPTS:
                AuditLogService.log(
                    action=AuditAction.STUDENT_ACCOUNT_OTP_FAILED,
                    target=request_obj,
                    new_value={"reason": "max_attempts", "request_id": str(request_obj.public_id)},
                    request=request,
                )
            raise ValidationError({"code": "Invalid OTP code."})

        with transaction.atomic():
            request_obj = (
                StudentAccountRequest.objects.select_for_update()
                .select_related("university", "faculty", "major", "reviewed_by")
                .get(pk=request_obj.pk, is_deleted=False)
            )
            user = StudentAccountRequestService.activate_account(request_obj)
            request_obj.otp_verified_at = timezone.now()
            request_obj.clear_otp()
            request_obj.status = StudentAccountRequestStatus.ACTIVE
            request_obj.activated_at = timezone.now()
            request_obj.created_user = user
            request_obj.save()

        AuditLogService.log(
            actor=user,
            action=AuditAction.STUDENT_ACCOUNT_OTP_VERIFIED,
            target=request_obj,
            new_value={"status": request_obj.status, "request_id": str(request_obj.public_id)},
            request=request,
        )
        AuditLogService.log(
            actor=user,
            action=AuditAction.STUDENT_ACCOUNT_ACTIVATED,
            target=request_obj,
            new_value={"user_id": user.id, "request_id": str(request_obj.public_id)},
            request=request,
        )
        return request_obj

    @staticmethod
    def activate_account(request_obj: StudentAccountRequest) -> User:
        if User.objects.filter(email__iexact=request_obj.email).exists():
            raise ValidationError({"email": "A user with this email already exists."})
        if User.objects.filter(phone_number=request_obj.phone_number).exists():
            raise ValidationError({"phone_number": "A user with this phone number already exists."})
        if StudentProfile.objects.filter(
            university=request_obj.university,
            student_number=request_obj.student_number,
            verification_status=StudentVerificationStatus.APPROVED,
        ).exists():
            raise ValidationError({"student_number": "This student number is already registered for this university."})

        user = User(
            full_name=request_obj.full_name,
            email=request_obj.email.lower(),
            phone_number=request_obj.phone_number,
            role=UserRole.STUDENT,
            is_active=True,
            is_phone_verified=True,
        )
        user.password = request_obj.password_hash
        user.save()

        now = timezone.now()
        profile = StudentProfile(
            user=user,
            university=request_obj.university,
            faculty=request_obj.faculty,
            major=request_obj.major,
            student_number=request_obj.student_number,
            verification_status=StudentVerificationStatus.APPROVED,
            verified_at=now,
            verification_reviewed_by=request_obj.reviewed_by,
            verification_reviewed_at=request_obj.reviewed_at or now,
        )

        extension = Path(request_obj.uploaded_card.name).suffix.lower().lstrip(".")
        image_extensions = {item.lower().lstrip(".") for item in settings.ALLOWED_IMAGE_EXTENSIONS}
        if extension in image_extensions and request_obj.uploaded_card:
            profile.card_image.save(
                Path(request_obj.uploaded_card.name).name,
                ContentFile(request_obj.uploaded_card.read()),
                save=False,
            )

        profile.save()
        if request_obj.student_number:
            apply_student_number_parse(profile, request_obj.student_number, auto_link_faculty=True)
            profile.save()

        NotificationService.create_notification(
            user,
            title="Account activated",
            body="Your student account has been activated. You can log in now.",
            type=NotificationType.SYSTEM,
            data={"student_account_request_id": str(request_obj.public_id), "status": StudentAccountRequestStatus.ACTIVE},
        )
        return user

    @staticmethod
    def create_card_preview_token(request_obj: StudentAccountRequest, reviewer, request=None) -> dict:
        token, expires_in = ProtectedMediaService.create_token(
            user=reviewer,
            object_type="student_account_request",
            object_id=request_obj.id,
            purpose="student_account_card_preview",
        )
        AuditLogService.log(
            actor=reviewer,
            action=AuditAction.STUDENT_ACCOUNT_CARD_PREVIEW_TOKEN_CREATED,
            target=request_obj,
            new_value={"request_id": str(request_obj.public_id)},
            request=request,
        )
        return {"token": token, "expires_in": expires_in}