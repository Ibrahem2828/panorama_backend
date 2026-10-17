from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.permissions import Capability, PermissionService
from apps.groups.models import GroupMembership, GroupMembershipStatus

from .models import Lecture, LecturePage, LectureProcessingStatus, LectureViewerSession


def user_can_manage_lectures(user) -> bool:
    return PermissionService.has(user, Capability.LECTURES_MANAGE)


def _subject_group_member_subjects(user):
    """Subject ids of the active groups where ``user`` is an approved member."""

    return GroupMembership.objects.filter(
        user=user,
        status=GroupMembershipStatus.APPROVED,
        is_deleted=False,
        group__is_deleted=False,
        group__is_active=True,
        group__subject__isnull=False,
    ).values("group__subject_id")


def user_can_view_lecture(user, lecture: Lecture) -> bool:
    """Enforce curriculum eligibility rather than relying on an obscured URL."""

    if not user or not user.is_authenticated or not user.is_active or lecture.is_deleted:
        return False
    if user.role in {UserRole.ADMIN, UserRole.IT_SUPPORT} or user_can_manage_lectures(user):
        return True
    if user.role != UserRole.STUDENT or not lecture.is_ready_for_students:
        return False
    profile = getattr(user, "student_profile", None)
    subject = lecture.subject
    if subject is None:
        # General lecture: every approved student, or only the students of one faculty when it is narrowed.
        return bool(
            profile
            and profile.verification_status == StudentVerificationStatus.APPROVED
            and (lecture.faculty_id is None or lecture.faculty_id == profile.faculty_id)
        )
    return bool(
        profile
        and profile.verification_status == StudentVerificationStatus.APPROVED
        and profile.faculty_id is not None
        and profile.faculty_id == subject.faculty_id
        and (subject.major_id is None or subject.major_id == profile.major_id)
        # Lectures belong to the subject's study group: only approved members of that group may read them.
        and _subject_group_member_subjects(user).filter(group__subject_id=subject.id).exists()
    )


def accessible_lectures_for_user(user):
    queryset = Lecture.objects.filter(is_deleted=False).select_related(
        "faculty",
        "subject",
        "subject__faculty",
        "subject__major",
        "subject__academic_year",
        "subject__semester",
        "uploaded_by",
    )
    if user_can_manage_lectures(user):
        return queryset
    if not user or not user.is_authenticated or user.role != UserRole.STUDENT:
        return queryset.none()
    profile = getattr(user, "student_profile", None)
    if not profile or profile.verification_status != StudentVerificationStatus.APPROVED:
        return queryset.none()
    # General lectures (no subject) need no faculty on the profile unless they are narrowed to a faculty.
    general = Q(subject__isnull=True) & (
        Q(faculty__isnull=True) | (Q(faculty_id=profile.faculty_id) if profile.faculty_id else Q(pk__in=[]))
    )
    subject_lectures = Q(pk__in=[])
    if profile.faculty_id:
        # Subjects belong to the faculty; the major is an optional tag. Any year/semester of the faculty is open to
        # an approved student (a year-2 student may retake a year-1 subject), but only through the subject's group.
        subject_scope = Q(subject__major__isnull=True)
        if profile.major_id:
            subject_scope |= Q(subject__major_id=profile.major_id)
        subject_lectures = subject_scope & Q(
            subject__faculty_id=profile.faculty_id,
            subject_id__in=_subject_group_member_subjects(user),
        )
    return queryset.filter(general | subject_lectures, is_published=True, status="ready")


def issue_viewer_session(user, lecture: Lecture) -> LectureViewerSession:
    if not user_can_view_lecture(user, lecture):
        raise NotFound("Lecture is unavailable.")
    return LectureViewerSession.issue(lecture, user)


def consume_viewer_page_session(user, lecture: Lecture, token: str) -> LectureViewerSession:
    """Atomically validate and account for a short-lived viewer session."""

    try:
        with transaction.atomic():
            session = (
                LectureViewerSession.objects.select_for_update()
                .select_related("lecture", "lecture__subject", "user")
                .filter(token=token, lecture=lecture, user=user, is_deleted=False)
                .first()
            )
            if not session or not session.is_valid or not user_can_view_lecture(user, session.lecture):
                raise NotFound("Viewer session is invalid or expired.")
            session.page_request_count += 1
            session.save(update_fields=["page_request_count", "updated_at"])
            return session
    except ValueError as exc:
        raise NotFound("Viewer session is invalid or expired.") from exc


def get_authorized_page(user, lecture: Lecture, token: str, page_number: int) -> LecturePage:
    consume_viewer_page_session(user, lecture, token)
    page = LecturePage.objects.filter(
        lecture=lecture,
        page_number=page_number,
        is_deleted=False,
    ).first()
    if not page or not page.rendered_file:
        raise NotFound("Lecture page is unavailable.")
    return page


#: A lecture that has sat in one of these states without progress is considered stalled (worker or dispatch lost).
STALLED_STATUSES = (
    LectureProcessingStatus.UPLOADED,
    LectureProcessingStatus.QUEUED,
    LectureProcessingStatus.SCANNING,
    LectureProcessingStatus.CONVERTING,
    LectureProcessingStatus.EXTRACTING,
    LectureProcessingStatus.RENDERING,
)
STALLED_AFTER = timedelta(minutes=30)  # same horizon as ``fail_stuck_lectures``


def lecture_is_retryable(lecture: Lecture) -> bool:
    """Failed lectures, and in-flight ones that made no progress for 30 minutes. Quarantined and ready ones never."""

    if lecture.status == LectureProcessingStatus.FAILED:
        return True
    return lecture.status in STALLED_STATUSES and lecture.updated_at < timezone.now() - STALLED_AFTER


def require_lecture_manager(user) -> None:
    if not user_can_manage_lectures(user):
        raise PermissionDenied("You do not have permission to manage lectures.")
