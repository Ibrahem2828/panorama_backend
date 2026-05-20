from django.db.models import Q
from django.utils import timezone

from apps.accounts.choices import StudentVerificationStatus, UserRole

from .models import Announcement, AnnouncementTargetUserType


def announcements_for_user(user):
    now = timezone.now()
    queryset = Announcement.objects.filter(is_active=True, is_deleted=False).filter(
        Q(starts_at__isnull=True) | Q(starts_at__lte=now),
        Q(ends_at__isnull=True) | Q(ends_at__gte=now),
    )
    if user.role == UserRole.NORMAL_USER:
        return queryset.filter(target_user_type__in=[AnnouncementTargetUserType.ALL, AnnouncementTargetUserType.NORMAL_USERS])
    if user.role == UserRole.STUDENT:
        profile = getattr(user, "student_profile", None)
        target_types = [AnnouncementTargetUserType.ALL, AnnouncementTargetUserType.STUDENTS]
        if profile and profile.verification_status == StudentVerificationStatus.APPROVED:
            target_types.append(AnnouncementTargetUserType.VERIFIED_STUDENTS)
        queryset = queryset.filter(target_user_type__in=target_types)
        if profile:
            return queryset.filter(
                Q(target_university__isnull=True) | Q(target_university=profile.university),
                Q(target_faculty__isnull=True) | Q(target_faculty=profile.faculty),
                Q(target_major__isnull=True) | Q(target_major=profile.major),
                Q(target_academic_year__isnull=True) | Q(target_academic_year=profile.academic_year),
                Q(target_semester__isnull=True) | Q(target_semester=profile.semester),
            )
        return queryset.filter(
            target_university__isnull=True,
            target_faculty__isnull=True,
            target_major__isnull=True,
            target_academic_year__isnull=True,
            target_semester__isnull=True,
        )
    if user.role in {UserRole.ADMIN, UserRole.IT_SUPPORT}:
        return queryset.filter(target_user_type__in=[AnnouncementTargetUserType.ALL, AnnouncementTargetUserType.ADMINS])
    return queryset.filter(target_user_type=AnnouncementTargetUserType.ALL)
