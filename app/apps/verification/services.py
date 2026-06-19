from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.accounts.choices import StudentVerificationStatus
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.protected_media import ProtectedMediaService
from apps.notifications.models import NotificationType
from apps.notifications.services import NotificationService

from .models import VerificationRequest, VerificationStatus


class VerificationService:
    @staticmethod
    @transaction.atomic
    def review(
        verification: VerificationRequest,
        reviewer,
        status: str,
        rejection_reason: str = "",
        admin_note: str = "",
        request=None,
    ):
        verification = (
            VerificationRequest.objects.select_for_update()
            .select_related("user", "student_profile", "university", "faculty", "major", "academic_year", "semester")
            .get(pk=verification.pk, is_deleted=False)
        )
        if verification.status != VerificationStatus.PENDING:
            raise ValidationError("Only pending verification requests can be reviewed.")
        if verification.user_id == reviewer.id:
            raise ValidationError("You cannot review your own verification request.")

        now = timezone.now()
        verification.status = status
        verification.rejection_reason = rejection_reason
        verification.admin_note = admin_note
        verification.reviewed_by = reviewer
        verification.reviewed_at = now
        verification.save()

        profile = verification.student_profile
        profile.verification_reviewed_by = reviewer
        profile.verification_reviewed_at = now

        if status == VerificationStatus.APPROVED:
            profile.verification_status = StudentVerificationStatus.APPROVED
            profile.verified_at = now
            profile.university = verification.university
            profile.faculty = verification.faculty
            profile.major = verification.major
            profile.academic_year = verification.academic_year
            profile.semester = verification.semester
            profile.student_number = verification.student_number
            title = "Verification approved"
            body = "Your student verification request was approved."
        elif status == VerificationStatus.NEEDS_UPDATE:
            profile.verification_status = StudentVerificationStatus.NEEDS_UPDATE
            title = "Verification needs update"
            body = rejection_reason or "Please update your verification request."
        else:
            profile.verification_status = StudentVerificationStatus.REJECTED
            title = "Verification rejected"
            body = rejection_reason or "Your student verification request was rejected."

        profile.save()
        NotificationService.create_notification(
            verification.user,
            title=title,
            body=body,
            type=NotificationType.VERIFICATION,
            data={"verification_request_id": verification.id, "status": status},
        )
        action = {
            VerificationStatus.APPROVED: AuditAction.VERIFICATION_APPROVED,
            VerificationStatus.REJECTED: AuditAction.VERIFICATION_REJECTED,
            VerificationStatus.NEEDS_UPDATE: AuditAction.VERIFICATION_NEEDS_UPDATE,
        }.get(status)
        if action:
            AuditLogService.log(
                actor=reviewer,
                action=action,
                target=verification,
                new_value={"status": status, "student_number": verification.student_number},
                request=request,
            )
        return verification

    @staticmethod
    def create_card_preview_token(verification: VerificationRequest, reviewer, request=None) -> dict:
        token, expires_in = ProtectedMediaService.create_token(
            user=reviewer,
            object_type="verification_request",
            object_id=verification.id,
            purpose="verification_card_preview",
        )
        AuditLogService.log(
            actor=reviewer,
            action=AuditAction.VERIFICATION_CARD_PREVIEW_TOKEN_CREATED,
            target=verification,
            new_value={"purpose": "verification_card_preview", "expires_in": expires_in},
            request=request,
        )
        return {"url": ProtectedMediaService.build_url(request, token), "expires_in": expires_in}
