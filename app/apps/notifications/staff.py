"""Staff inbox: tell the people who hold a capability that something needs them.

Every call is best effort: a failing notification must never fail the business action that triggered it.
"""

from __future__ import annotations

import logging

from django.db import IntegrityError, transaction

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.accounts.permissions import PermissionService

from .models import NotificationType
from .services import NotificationService

logger = logging.getLogger(__name__)

#: kind -> (capability that owns the event, dashboard page the notification opens)
STAFF_EVENTS = {
    "enrollment": ("enrollment.review", "/dashboard/enrollment"),
    "verification_request": ("verification.review", "/dashboard/verifications"),
    "chat_report": ("groups.manage", "/dashboard/chat-reports"),
    "support_ticket": ("support.manage", "/dashboard/support"),
    "print_order": ("printing.manage", "/dashboard/printing"),
    "purchase_reminder": ("finance.manage", "/dashboard/finance/purchases"),
    "student_registration": ("verification.review", "/dashboard/verifications"),
    "join_request": ("groups.manage", "/dashboard/groups"),
}

_TYPE_FOR_KIND = {
    "enrollment": NotificationType.GROUP,
    "verification_request": NotificationType.VERIFICATION,
    "chat_report": NotificationType.GROUP,
    "support_ticket": NotificationType.SUPPORT,
    "print_order": NotificationType.PRINTING,
    "purchase_reminder": NotificationType.SYSTEM,
    "student_registration": NotificationType.VERIFICATION,
    "join_request": NotificationType.GROUP,
}


def staff_with(capability: str, *, exclude_user_id: int | None = None) -> list[User]:
    candidates = User.objects.filter(is_active=True, is_deleted=False).exclude(
        role__in=(UserRole.STUDENT, UserRole.NORMAL_USER)
    )
    if exclude_user_id is not None:
        candidates = candidates.exclude(pk=exclude_user_id)
    return [user for user in candidates if PermissionService.has(user, capability)]


def notify_staff(
    kind: str,
    title: str,
    body: str,
    *,
    object_id: int | str,
    actor_id: int | None = None,
    extra: dict | None = None,
    highlight: int | str | None = None,
) -> int:
    """Create one inbox notification per staff member who can act on ``kind``; returns how many were created.

    ``object_id`` identifies the event (and is the de-duplication key); ``highlight`` is what the dashboard page should
    highlight when it differs (a join request opens its group, but every request is its own event).
    """

    capability, page = STAFF_EVENTS[kind]
    sent = 0
    try:
        for user in staff_with(capability, exclude_user_id=actor_id):
            try:
                with transaction.atomic():
                    notification = NotificationService.create_notification(
                        user,
                        title,
                        body,
                        type=_TYPE_FOR_KIND[kind],
                        data={"kind": kind, "object_id": str(object_id), **(extra or {})},
                        deep_link=f"{page}?highlight={object_id if highlight is None else highlight}",
                        related_object_type=kind,
                        related_object_id=str(object_id),
                        deduplication_key=f"{kind}:{object_id}",
                    )
            except IntegrityError:
                continue  # this person was already told about this object
            sent += 1 if notification is not None else 0
    except Exception:
        logger.exception("staff_notification_failed", extra={"kind": kind, "object_id": str(object_id)})
    return sent
