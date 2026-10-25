"""Notification categories: what the inbox groups by, which Android channel a push uses and which pages staff land on.

A notification's category is derived from its type and, for staff-facing events, from ``data["kind"]``.
"""

from __future__ import annotations

from .models import NotificationType

CHAT = "chat"
GROUPS = "groups"
PRINTING = "printing"
VERIFICATION = "verification"
SUPPORT = "support"
ENROLLMENT = "enrollment"
REPORTS = "reports"
PURCHASES = "purchases"
GENERAL = "general"

#: Channels a member can switch off one by one (the mobile settings screen lists exactly these).
PREFERENCE_CHANNELS = (CHAT, GROUPS, PRINTING, VERIFICATION, SUPPORT)

#: ``data["kind"]`` values of staff inbox events: a burst of them is folded into one push (members' own notices never are).
STAFF_KINDS = frozenset(
    {"enrollment", "chat_report", "purchase_reminder", "support_ticket", "print_order", "verification_request"}
)

_KIND_TO_CATEGORY = {
    "enrollment": ENROLLMENT,
    "chat_report": REPORTS,
    "purchase_reminder": PURCHASES,
    "support_ticket": SUPPORT,
    "print_order": PRINTING,
    "verification_request": VERIFICATION,
    "chat": CHAT,
}

_TYPE_TO_CATEGORY = {
    NotificationType.CHAT: CHAT,
    NotificationType.GROUP: GROUPS,
    NotificationType.PRINTING: PRINTING,
    NotificationType.VERIFICATION: VERIFICATION,
    NotificationType.SUPPORT: SUPPORT,
}

#: Android notification channel ids created by the app (``general`` is the catch-all; it carries the Panorama chime, ``default`` is only the legacy channel of older app versions).
_CATEGORY_TO_CHANNEL = {
    CHAT: "chat",
    GROUPS: "groups",
    ENROLLMENT: "groups",
    PRINTING: "printing",
    VERIFICATION: "verification",
    SUPPORT: "support",
    REPORTS: "general",
    PURCHASES: "general",
    GENERAL: "general",
}

#: The preference switch that governs a category (categories without their own switch follow their closest one).
_CATEGORY_TO_PREFERENCE = {
    ENROLLMENT: GROUPS,
    REPORTS: GROUPS,
    PURCHASES: None,
    GENERAL: None,
}


def category_for(notification_type: str, data: dict | None = None) -> str:
    kind = str((data or {}).get("kind") or "")
    if kind in _KIND_TO_CATEGORY:
        return _KIND_TO_CATEGORY[kind]
    return _TYPE_TO_CATEGORY.get(notification_type, GENERAL)  # type: ignore[call-overload]


def android_channel_for(category: str) -> str:
    return _CATEGORY_TO_CHANNEL.get(category, "general")


def preference_channel_for(category: str) -> str | None:
    """The switch in ``NotificationPreference.channels`` that governs ``category`` (None: not switchable)."""

    if category in PREFERENCE_CHANNELS:
        return category
    return _CATEGORY_TO_PREFERENCE.get(category)
