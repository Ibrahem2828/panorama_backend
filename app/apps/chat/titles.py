"""What other members see above an administrator's message: their position, never their name."""

from __future__ import annotations

from apps.accounts.choices import UserRole
from apps.accounts.permissions import Capability, PermissionService

TITLES = {
    UserRole.IT_SUPPORT: "بانوراما مانجمنت آي تي",
    UserRole.ADMIN: "بانوراما أدمن",
    UserRole.CONTENT_MANAGER: "بانوراما كونفرزيشن أدمن",
    UserRole.PRINT_STAFF: "بانوراما الطباعة",
    UserRole.SUPPORT_STAFF: "بانوراما الدعم",
}
CONVERSATION_ADMIN_TITLE = TITLES[UserRole.CONTENT_MANAGER]


def chat_title(user) -> str | None:
    """The position shown instead of the name, or None for an ordinary student or user."""

    title = TITLES.get(user.role)
    if title:
        return title
    if user.role in (UserRole.STUDENT, UserRole.NORMAL_USER):
        return None
    return CONVERSATION_ADMIN_TITLE if PermissionService.has(user, Capability.GROUPS_MANAGE) else None
