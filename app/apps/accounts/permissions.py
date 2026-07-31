from __future__ import annotations

from django.db import models
from django.utils import timezone
from rest_framework.permissions import BasePermission

from .choices import PermissionEffect, StudentVerificationStatus, UserRole
from .models import UserPermissionOverride


class Capability:
    DASHBOARD_ACCESS = "dashboard.access"
    SYSTEM_MANAGE = "system.manage"
    USERS_MANAGE = "users.manage"
    ACADEMIC_MANAGE = "academic.manage"
    VERIFICATION_REVIEW = "verification.review"
    GROUPS_MANAGE = "groups.manage"
    GROUPS_EXTERNAL_CHANNELS_MANAGE = "groups.external_channels.manage"
    FILES_MANAGE = "files.manage"
    LECTURES_MANAGE = "lectures.manage"
    PRINTING_MANAGE = "printing.manage"
    SUPPORT_MANAGE = "support.manage"
    ANNOUNCEMENTS_MANAGE = "announcements.manage"
    AUDIT_VIEW = "audit.view"
    FEEDBACK_MANAGE = "feedback.manage"
    PRODUCT_MANAGE = "product.manage"


ROLE_CAPABILITIES = {
    UserRole.IT_SUPPORT: {"*"},
    UserRole.ADMIN: {
        Capability.DASHBOARD_ACCESS,
        Capability.USERS_MANAGE,
        Capability.ACADEMIC_MANAGE,
        Capability.VERIFICATION_REVIEW,
        Capability.GROUPS_MANAGE,
        Capability.GROUPS_EXTERNAL_CHANNELS_MANAGE,
        Capability.FILES_MANAGE,
        Capability.LECTURES_MANAGE,
        Capability.PRINTING_MANAGE,
        Capability.SUPPORT_MANAGE,
        Capability.ANNOUNCEMENTS_MANAGE,
        Capability.AUDIT_VIEW,
        Capability.FEEDBACK_MANAGE,
        Capability.PRODUCT_MANAGE,
    },
    UserRole.PRINT_STAFF: {Capability.DASHBOARD_ACCESS, Capability.PRINTING_MANAGE},
    UserRole.SUPPORT_STAFF: {Capability.DASHBOARD_ACCESS, Capability.SUPPORT_MANAGE, Capability.FEEDBACK_MANAGE},
    UserRole.CONTENT_MANAGER: {
        Capability.DASHBOARD_ACCESS,
        Capability.ACADEMIC_MANAGE,
        Capability.GROUPS_MANAGE,
        Capability.GROUPS_EXTERNAL_CHANNELS_MANAGE,
        Capability.FILES_MANAGE,
        Capability.LECTURES_MANAGE,
        Capability.ANNOUNCEMENTS_MANAGE,
    },
    UserRole.STUDENT: set(),
    UserRole.NORMAL_USER: set(),
}


class PermissionService:
    @staticmethod
    def has(user, permission_code: str) -> bool:
        if not user or not user.is_authenticated or not user.is_active:
            return False
        override = (
            UserPermissionOverride.objects.filter(
                user=user,
                permission_code=permission_code,
                is_deleted=False,
            )
            .filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=timezone.now()))
            .order_by("-updated_at")
            .first()
        )
        if override:
            return override.effect == PermissionEffect.ALLOW
        defaults = ROLE_CAPABILITIES.get(user.role, set())
        return "*" in defaults or permission_code in defaults


class RolePermission(BasePermission):
    role: str | None = None

    def has_permission(self, request, view) -> bool:
        return bool(request.user and request.user.is_authenticated and request.user.role == self.role)


class CapabilityPermission(BasePermission):
    capability: str = ""

    def has_permission(self, request, view) -> bool:
        return PermissionService.has(request.user, self.capability)


class IsITSupport(RolePermission):
    role = UserRole.IT_SUPPORT


class IsAdmin(RolePermission):
    role = UserRole.ADMIN


class IsPrintStaff(RolePermission):
    role = UserRole.PRINT_STAFF


class IsStudent(RolePermission):
    role = UserRole.STUDENT


class IsNormalUser(RolePermission):
    role = UserRole.NORMAL_USER


class IsAdminOrITSupport(BasePermission):
    def has_permission(self, request, view) -> bool:
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role in {UserRole.ADMIN, UserRole.IT_SUPPORT}
        )


class IsPrintStaffOrAdmin(CapabilityPermission):
    capability = Capability.PRINTING_MANAGE


class CanAccessDashboard(CapabilityPermission):
    capability = Capability.DASHBOARD_ACCESS


class CanManageUsers(CapabilityPermission):
    capability = Capability.USERS_MANAGE


class CanManageAcademic(CapabilityPermission):
    capability = Capability.ACADEMIC_MANAGE


class CanReviewVerification(CapabilityPermission):
    capability = Capability.VERIFICATION_REVIEW


class CanManageGroups(CapabilityPermission):
    capability = Capability.GROUPS_MANAGE


class CanManageExternalChannels(CapabilityPermission):
    capability = Capability.GROUPS_EXTERNAL_CHANNELS_MANAGE


class CanManageFiles(CapabilityPermission):
    capability = Capability.FILES_MANAGE


class CanManageLectures(CapabilityPermission):
    capability = Capability.LECTURES_MANAGE


class CanManagePrinting(CapabilityPermission):
    capability = Capability.PRINTING_MANAGE


class CanManageSupport(CapabilityPermission):
    capability = Capability.SUPPORT_MANAGE


class CanManageAnnouncements(CapabilityPermission):
    capability = Capability.ANNOUNCEMENTS_MANAGE


class CanViewAudit(CapabilityPermission):
    capability = Capability.AUDIT_VIEW


class CanManageFeedback(CapabilityPermission):
    capability = Capability.FEEDBACK_MANAGE


class CanManageProduct(CapabilityPermission):
    capability = Capability.PRODUCT_MANAGE


class IsVerifiedStudent(BasePermission):
    def has_permission(self, request, view) -> bool:
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.role != UserRole.STUDENT:
            return False
        profile = getattr(request.user, "student_profile", None)
        return bool(profile and profile.verification_status == StudentVerificationStatus.APPROVED)
