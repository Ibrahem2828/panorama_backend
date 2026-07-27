from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.accounts.choices import StudentVerificationStatus
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
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
            .select_related("student_profile", "user")
            .get(pk=verification.pk, is_deleted=False)
        )
        if verification.status != VerificationStatus.PENDING:
            raise ValidationError("Only pending verification requests can be reviewed.")
        if verification.user_id == reviewer.id:
            raise ValidationError("You cannot review your own verification request.")
        if status not in {
            VerificationStatus.APPROVED,
            VerificationStatus.REJECTED,
            VerificationStatus.NEEDS_UPDATE,
        }:
            raise ValidationError({"status": "Unsupported verification decision."})
        if status in {VerificationStatus.REJECTED, VerificationStatus.NEEDS_UPDATE} and not rejection_reason.strip():
            raise ValidationError({"rejection_reason": "A clear reason is required for this decision."})

        now = timezone.now()
        verification.status = status
        verification.rejection_reason = rejection_reason.strip()
        verification.admin_note = admin_note.strip()
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
            title = "تم توثيق حسابك"
            body = "أصبح بإمكانك الآن استخدام ميزات الطلاب الموثقين في بانوراما."
        elif status == VerificationStatus.NEEDS_UPDATE:
            profile.verification_status = StudentVerificationStatus.NEEDS_UPDATE
            profile.verified_at = None
            title = "طلب التوثيق يحتاج إلى تحديث"
            body = rejection_reason.strip()
        else:
            profile.verification_status = StudentVerificationStatus.REJECTED
            profile.verified_at = None
            title = "تم رفض طلب التوثيق"
            body = rejection_reason.strip()

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
        }[status]
        AuditLogService.log(
            actor=reviewer,
            action=action,
            target=verification,
            new_value={"status": status, "student_number": verification.student_number},
            request=request,
        )
        return verification
