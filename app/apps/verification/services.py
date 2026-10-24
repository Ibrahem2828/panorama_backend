from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError

from apps.accounts.choices import StudentVerificationStatus
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.notifications.models import NotificationType
from apps.notifications.services import NotificationService

from .models import VerificationRequest, VerificationStatus


class VerificationStateConflict(APIException):
    """The student's current verification state does not allow a new submission (HTTP 409, stable ``api_code``)."""

    status_code = 409
    default_detail = "The verification request cannot be submitted in the current state."
    default_code = "verification_state_conflict"

    def __init__(self, detail=None, *, api_code: str = "VERIFICATION_STATE_CONFLICT"):
        super().__init__(detail)
        self.api_code = api_code


# Only these profile states may open a new request. ``approved`` needs staff to reopen it first (the dashboard's
# "needs update" decision accepts an approved request), ``pending`` already has one in review.
SUBMITTABLE_STATES = frozenset(
    {
        StudentVerificationStatus.INCOMPLETE,
        StudentVerificationStatus.REJECTED,
        StudentVerificationStatus.NEEDS_UPDATE,
    }
)


def assert_can_submit_verification(profile) -> None:
    status = profile.verification_status
    if status in SUBMITTABLE_STATES:
        return
    if status == StudentVerificationStatus.PENDING:
        raise VerificationStateConflict(
            "You already have a pending verification request.", api_code="VERIFICATION_ALREADY_PENDING"
        )
    if status == StudentVerificationStatus.APPROVED:
        raise VerificationStateConflict(
            "Your account is already verified. Contact support if the details must change.",
            api_code="VERIFICATION_ALREADY_APPROVED",
        )
    raise VerificationStateConflict(
        "A new verification request cannot be submitted right now.", api_code="VERIFICATION_NOT_RESUBMITTABLE"
    )


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
        # A decision applies to a pending request. The one exception is "needs update", which also reopens an
        # approved request so the student may resubmit (otherwise an approved student can never be sent back).
        reviewable = {VerificationStatus.PENDING}
        if status == VerificationStatus.NEEDS_UPDATE:
            reviewable.add(VerificationStatus.APPROVED)
        if verification.status not in reviewable:
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
            title_en = "Your account is verified"
            body_en = "You can now use the verified-student features in Panorama."
            deep_link = "/home"
        elif status == VerificationStatus.NEEDS_UPDATE:
            profile.verification_status = StudentVerificationStatus.NEEDS_UPDATE
            profile.verified_at = None
            title = "طلب التوثيق يحتاج إلى تحديث"
            body = rejection_reason.strip()
            title_en = "Your verification request needs an update"
            body_en = body
            deep_link = "/setup/verification/status"
        else:
            profile.verification_status = StudentVerificationStatus.REJECTED
            profile.verified_at = None
            title = "تم رفض طلب التوثيق"
            body = rejection_reason.strip()
            title_en = "Your verification request was rejected"
            body_en = body
            deep_link = "/setup/verification/status"

        profile.save()
        if status == VerificationStatus.APPROVED:
            from apps.enrollment.services import FacultyCommunityService

            FacultyCommunityService.join(verification.user)
        NotificationService.create_notification(
            verification.user,
            title=title,
            body=body,
            type=NotificationType.VERIFICATION,
            data={"verification_request_id": verification.id, "status": status},
            title_ar=title,
            title_en=title_en,
            body_ar=body,
            body_en=body_en,
            deep_link=deep_link,
        )
        action = {
            VerificationStatus.APPROVED: AuditAction.VERIFICATION_APPROVED,
            VerificationStatus.REJECTED: AuditAction.VERIFICATION_REJECTED,
            VerificationStatus.NEEDS_UPDATE: AuditAction.VERIFICATION_NEEDS_UPDATE,
        }[VerificationStatus(status)]
        AuditLogService.log(
            actor=reviewer,
            action=action,
            target=verification,
            new_value={"status": status, "student_number": verification.student_number},
            request=request,
        )
        return verification
