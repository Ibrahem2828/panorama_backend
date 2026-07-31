from __future__ import annotations

from django.db import transaction
from rest_framework.exceptions import NotFound, PermissionDenied

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.permissions import Capability, PermissionService

from .models import Lecture, LecturePage, LectureViewerSession


def user_can_manage_lectures(user) -> bool:
    return PermissionService.has(user, Capability.LECTURES_MANAGE)


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
    return bool(
        profile
        and profile.verification_status == StudentVerificationStatus.APPROVED
        and profile.major_id == subject.major_id
        and profile.academic_year_id == subject.academic_year_id
        and profile.semester_id == subject.semester_id
    )


def accessible_lectures_for_user(user):
    queryset = Lecture.objects.filter(is_deleted=False).select_related(
        "subject",
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
    return queryset.filter(
        subject__major_id=profile.major_id,
        subject__academic_year_id=profile.academic_year_id,
        subject__semester_id=profile.semester_id,
        is_published=True,
        status="ready",
    )


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


def require_lecture_manager(user) -> None:
    if not user_can_manage_lectures(user):
        raise PermissionDenied("You do not have permission to manage lectures.")
