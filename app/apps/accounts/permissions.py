from rest_framework.permissions import BasePermission

from .choices import StudentVerificationStatus, UserRole


class RolePermission(BasePermission):
    role: str | None = None

    def has_permission(self, request, view) -> bool:
        return bool(request.user and request.user.is_authenticated and request.user.role == self.role)


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


class IsPrintStaffOrAdmin(BasePermission):
    def has_permission(self, request, view) -> bool:
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role in {UserRole.PRINT_STAFF, UserRole.ADMIN, UserRole.IT_SUPPORT}
        )


class IsVerifiedStudent(BasePermission):
    def has_permission(self, request, view) -> bool:
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.role != UserRole.STUDENT:
            return False
        profile = getattr(request.user, "student_profile", None)
        return bool(profile and profile.verification_status == StudentVerificationStatus.APPROVED)
