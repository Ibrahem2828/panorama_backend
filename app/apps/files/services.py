from django.db.models import Q

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.protected_media import ProtectedMediaService
from apps.groups.models import GroupMembershipStatus

from .models import FileResource, FileVisibility


def user_can_access_file(user, file_resource: FileResource) -> bool:
    if not user.is_authenticated or not file_resource.is_active or file_resource.is_deleted:
        return False
    if user.role in {UserRole.ADMIN, UserRole.IT_SUPPORT}:
        return True
    if file_resource.visibility == FileVisibility.PUBLIC:
        return True
    if file_resource.visibility == FileVisibility.ADMIN_ONLY:
        return False
    if user.role != UserRole.STUDENT:
        return False
    profile = getattr(user, "student_profile", None)
    if file_resource.visibility == FileVisibility.STUDENTS_ONLY:
        return bool(profile)
    if not profile or profile.verification_status != StudentVerificationStatus.APPROVED:
        return False
    if file_resource.visibility == FileVisibility.VERIFIED_STUDENTS_ONLY:
        return True
    if file_resource.visibility == FileVisibility.MAJOR_ONLY:
        return profile.major_id == file_resource.major_id and profile.academic_year_id == file_resource.academic_year_id
    if file_resource.visibility == FileVisibility.GROUP_ONLY:
        return user.group_memberships.filter(
            group=file_resource.group,
            status=GroupMembershipStatus.APPROVED,
        ).exists()
    return False


def accessible_files_for_user(user):
    queryset = FileResource.objects.filter(is_active=True, is_deleted=False).select_related(
        "uploaded_by", "university", "faculty", "major", "academic_year", "semester", "subject", "group"
    )
    if user.role in {UserRole.ADMIN, UserRole.IT_SUPPORT}:
        return queryset
    base = Q(visibility=FileVisibility.PUBLIC)
    if user.role == UserRole.STUDENT:
        profile = getattr(user, "student_profile", None)
        base |= Q(visibility=FileVisibility.STUDENTS_ONLY)
        if profile and profile.verification_status == StudentVerificationStatus.APPROVED:
            group_ids = user.group_memberships.filter(status=GroupMembershipStatus.APPROVED).values_list("group_id", flat=True)
            base |= Q(visibility=FileVisibility.VERIFIED_STUDENTS_ONLY)
            base |= Q(visibility=FileVisibility.MAJOR_ONLY, major=profile.major, academic_year=profile.academic_year)
            base |= Q(visibility=FileVisibility.GROUP_ONLY, group_id__in=group_ids)
    return queryset.filter(base).distinct()


class FileAccessService:
    @staticmethod
    def create_download_token(user, file_resource: FileResource, request=None) -> dict:
        if not user_can_access_file(user, file_resource):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("You do not have access to this file.")
        token, expires_in = ProtectedMediaService.create_token(
            user=user,
            object_type="file_resource",
            object_id=file_resource.id,
            purpose="file_download",
        )
        AuditLogService.log(
            actor=user,
            action=AuditAction.FILE_DOWNLOAD_TOKEN_CREATED,
            target=file_resource,
            new_value={"purpose": "file_download", "expires_in": expires_in},
            request=request,
        )
        return {"url": ProtectedMediaService.build_url(request, token), "expires_in": expires_in}

    @staticmethod
    def create_dashboard_preview_token(user, file_resource: FileResource, request=None) -> dict:
        if user.role not in {UserRole.ADMIN, UserRole.IT_SUPPORT}:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("You do not have access to this file.")
        token, expires_in = ProtectedMediaService.create_token(
            user=user,
            object_type="file_resource",
            object_id=file_resource.id,
            purpose="file_preview",
        )
        AuditLogService.log(
            actor=user,
            action=AuditAction.FILE_PREVIEW_TOKEN_CREATED,
            target=file_resource,
            new_value={"purpose": "file_preview", "expires_in": expires_in},
            request=request,
        )
        return {"url": ProtectedMediaService.build_url(request, token), "expires_in": expires_in}
