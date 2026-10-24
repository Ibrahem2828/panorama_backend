from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import User
from apps.accounts.permissions import Capability, PermissionService
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.groups.models import Group, GroupMembership, GroupMembershipStatus
from apps.notifications.models import NotificationType
from apps.notifications.services import NotificationService
from apps.universities.models import Subject

from .models import EnrollmentItem, EnrollmentItemStatus, EnrollmentRequest, EnrollmentStatus

logger = logging.getLogger(__name__)

MAX_SUBJECTS_PER_REQUEST = 12
SEARCH_MIN_CHARS = 2
SEARCH_LIMIT = 8


class EnrollmentConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "This enrollment request cannot be changed in its current state."
    default_code = "enrollment_state_conflict"


# -- faculty community ------------------------------------------------------------------------------------------------


def faculty_community_groups(faculty_id: int | None):
    """The faculty-wide community group(s): active groups of the faculty that are not tied to one subject."""

    if not faculty_id:
        return Group.objects.none()
    return Group.objects.filter(faculty_id=faculty_id, subject__isnull=True, is_active=True, is_deleted=False).order_by(
        "id"
    )


def add_to_group(user, group: Group, reviewer=None) -> bool:
    """Make ``user`` an approved member (idempotent). Returns True when something changed; never lifts a block."""

    membership = GroupMembership.objects.select_for_update().filter(group=group, user=user).first()
    now = timezone.now()
    if membership is None:
        GroupMembership.objects.create(
            group=group,
            user=user,
            status=GroupMembershipStatus.APPROVED,
            reviewed_by=reviewer,
            reviewed_at=now,
            joined_at=now,
        )
        return True
    if membership.status == GroupMembershipStatus.BLOCKED or membership.status == GroupMembershipStatus.APPROVED:
        return False
    membership.status = GroupMembershipStatus.APPROVED
    membership.reviewed_by = reviewer
    membership.reviewed_at = now
    membership.joined_at = membership.joined_at or now
    membership.save()
    return True


class FacultyCommunityService:
    @staticmethod
    def join(user) -> int:
        """Add a verified student to their faculty community. Called when their verification is approved."""

        profile = getattr(user, "student_profile", None)
        if (
            user.role != UserRole.STUDENT
            or profile is None
            or profile.verification_status != StudentVerificationStatus.APPROVED
        ):
            return 0
        return sum(1 for group in faculty_community_groups(profile.faculty_id) if add_to_group(user, group))


# -- subject search -----------------------------------------------------------------------------------------------------


def search_subjects(user, query: str):
    """Type-ahead over the student's faculty. Shorter queries return nothing: the full list is never shown."""

    profile = getattr(user, "student_profile", None)
    needle = " ".join((query or "").split())
    if profile is None or not profile.faculty_id or len(needle) < SEARCH_MIN_CHARS:
        return Subject.objects.none()
    enrolled = GroupMembership.objects.filter(
        user=user, status=GroupMembershipStatus.APPROVED, group__subject__isnull=False
    ).values_list("group__subject_id", flat=True)
    pending = EnrollmentItem.objects.filter(
        request__user=user, request__status=EnrollmentStatus.PENDING, request__is_deleted=False
    ).values_list("subject_id", flat=True)
    return (
        Subject.objects.filter(faculty_id=profile.faculty_id, is_active=True, is_deleted=False)
        .filter(Q(name__icontains=needle) | Q(code__icontains=needle))
        .exclude(pk__in=list(enrolled))
        .exclude(pk__in=list(pending))
        .select_related("academic_year", "semester", "major")
        .order_by("name")[:SEARCH_LIMIT]
    )


# -- requests -----------------------------------------------------------------------------------------------------------


def reviewers():
    return [
        user
        for user in User.objects.filter(is_active=True, is_deleted=False).exclude(role=UserRole.STUDENT)
        if PermissionService.has(user, Capability.ENROLLMENT_REVIEW)
    ]


class EnrollmentService:
    @staticmethod
    @transaction.atomic
    def submit(*, user, screenshot, subject_ids: list[int], request=None) -> EnrollmentRequest:
        profile = getattr(user, "student_profile", None)
        if (
            user.role != UserRole.STUDENT
            or profile is None
            or profile.verification_status != StudentVerificationStatus.APPROVED
            or not profile.faculty_id
        ):
            raise ValidationError({"detail": "Only verified students can register subjects."})
        ids = list(dict.fromkeys(subject_ids))
        if not ids:
            raise ValidationError({"subject_ids": "Pick at least one subject."})
        if len(ids) > MAX_SUBJECTS_PER_REQUEST:
            raise ValidationError({"subject_ids": f"You can register at most {MAX_SUBJECTS_PER_REQUEST} subjects."})
        if EnrollmentRequest.objects.filter(user=user, status=EnrollmentStatus.PENDING, is_deleted=False).exists():
            raise EnrollmentConflict("You already have a request waiting for review.")
        subjects = list(
            Subject.objects.filter(pk__in=ids, faculty_id=profile.faculty_id, is_active=True, is_deleted=False)
        )
        if len(subjects) != len(ids):
            raise ValidationError({"subject_ids": "One of the subjects is not available for your faculty."})

        enrollment = EnrollmentRequest.objects.create(
            user=user,
            student_profile=profile,
            faculty_id=profile.faculty_id,
            academic_year_id=profile.academic_year_id,
            semester_id=profile.semester_id,
            screenshot=screenshot,
        )
        EnrollmentItem.objects.bulk_create(
            [EnrollmentItem(request=enrollment, subject=subject) for subject in subjects]
        )
        AuditLogService.log(
            actor=user,
            action=AuditAction.ENROLLMENT_SUBMITTED,
            target=enrollment,
            new_value={"subjects": len(subjects)},
            request=request,
        )
        transaction.on_commit(lambda: EnrollmentService.notify_reviewers(enrollment.pk))
        return enrollment

    @staticmethod
    def notify_reviewers(enrollment_id: int) -> None:
        enrollment = EnrollmentRequest.objects.select_related("user").filter(pk=enrollment_id).first()
        if enrollment is None:
            return
        for reviewer in reviewers():
            try:
                NotificationService.create_notification(
                    reviewer,
                    title="طلب تسجيل مواد جديد",
                    body=f"{enrollment.user.full_name} أرسل لقطة ليرناتا لمراجعتها.",
                    type=NotificationType.GROUP,
                    data={"kind": "enrollment", "enrollment_id": enrollment.pk},
                    deduplication_key=f"enrollment:{enrollment.pk}",
                    deep_link=f"/dashboard/enrollment?request={enrollment.pk}",
                )
            except Exception:
                logger.exception("enrollment_notify_failed", extra={"enrollment_id": enrollment_id})

    @staticmethod
    @transaction.atomic
    def cancel(enrollment: EnrollmentRequest, user, request=None) -> EnrollmentRequest:
        enrollment = EnrollmentRequest.objects.select_for_update().get(pk=enrollment.pk)
        if enrollment.user_id != user.pk:
            raise ValidationError({"detail": "This is not your request."})
        if enrollment.status != EnrollmentStatus.PENDING:
            raise EnrollmentConflict()
        enrollment.status = EnrollmentStatus.CANCELLED
        enrollment.save(update_fields=["status", "updated_at"])
        AuditLogService.log(actor=user, action=AuditAction.ENROLLMENT_CANCELLED, target=enrollment, request=request)
        return enrollment

    @staticmethod
    @transaction.atomic
    def decide(
        enrollment: EnrollmentRequest,
        reviewer,
        *,
        approve_item_ids: set[int] | None,
        reason: str = "",
        request=None,
    ) -> EnrollmentRequest:
        """``approve_item_ids=None`` approves every item; an empty set rejects every item; otherwise the rest are rejected."""

        enrollment = EnrollmentRequest.objects.select_for_update().select_related("user").get(pk=enrollment.pk)
        if enrollment.status != EnrollmentStatus.PENDING:
            raise EnrollmentConflict()
        if enrollment.user_id == reviewer.pk:
            raise ValidationError({"detail": "You cannot review your own request."})
        items = list(enrollment.items.select_related("subject"))
        approved_ids = {item.pk for item in items} if approve_item_ids is None else set(approve_item_ids)
        unknown = approved_ids - {item.pk for item in items}
        if unknown:
            raise ValidationError({"item_ids": "Some items do not belong to this request."})
        reason = (reason or "").strip()
        if len(approved_ids) < len(items) and not reason:
            raise ValidationError({"reason": "A reason is required when a subject is not approved."})

        now = timezone.now()
        enrolled_into = 0
        for item in items:
            if item.pk in approved_ids:
                item.status = EnrollmentItemStatus.APPROVED
                item.reason = ""
                for group in Group.objects.filter(subject=item.subject, is_active=True, is_deleted=False):
                    enrolled_into += 1 if add_to_group(enrollment.user, group, reviewer) else 0
            else:
                item.status = EnrollmentItemStatus.REJECTED
                item.reason = reason[:500]
            item.save(update_fields=["status", "reason", "updated_at"])

        approved_count = len(approved_ids)
        if approved_count == len(items):
            enrollment.status = EnrollmentStatus.APPROVED
        elif approved_count == 0:
            enrollment.status = EnrollmentStatus.REJECTED
        else:
            enrollment.status = EnrollmentStatus.PARTIALLY_APPROVED
        enrollment.review_note = reason
        enrollment.reviewed_by = reviewer
        enrollment.reviewed_at = now
        enrollment.save()

        AuditLogService.log(
            actor=reviewer,
            action=AuditAction.ENROLLMENT_REVIEWED,
            target=enrollment,
            new_value={"status": enrollment.status, "approved": approved_count, "of": len(items)},
            request=request,
            critical=True,
        )
        titles = {
            EnrollmentStatus.APPROVED: (
                "تم اعتماد تسجيل موادك",
                "أُضيفت إلى مجموعات موادك ويمكنك فتح الدردشة والملفات.",
            ),
            EnrollmentStatus.PARTIALLY_APPROVED: ("اعتُمد تسجيل بعض موادك", reason or "راجع المواد المرفوضة وأسبابها."),
            EnrollmentStatus.REJECTED: ("لم يُعتمد تسجيل موادك", reason),
        }
        title, body = titles[EnrollmentStatus(enrollment.status)]
        NotificationService.create_notification(
            enrollment.user,
            title=title,
            body=body,
            type=NotificationType.GROUP,
            data={"kind": "enrollment", "enrollment_id": enrollment.pk, "status": enrollment.status},
            deep_link="/groups/my",
        )
        return enrollment
