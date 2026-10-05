"""Erase personal data of a user whose account deletion is being executed.

Runs inside the caller's transaction. Business, financial and aggregate rows are kept but stripped of
personal content; files are removed from storage only after the transaction commits.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from django.db import models, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

REMOVED_TEXT = "[removed]"


def _schedule_file_deletion(field: models.Field, names: Iterable[str | None]) -> int:
    """Delete stored files after commit; a missing file or storage error never breaks the deletion."""

    unique = sorted({name for name in names if name})
    if not unique:
        return 0
    storage = field.storage  # type: ignore[attr-defined]

    def _delete() -> None:
        for name in unique:
            try:
                storage.delete(name)
            except Exception:
                logger.warning("Could not delete stored file during account anonymization", exc_info=True)

    transaction.on_commit(_delete)
    return len(unique)


def _clear_files(queryset: models.QuerySet, field_name: str, *, blank_value: str | None = "") -> int:
    """Collect stored file names, blank the column and schedule the storage deletions."""

    field = queryset.model._meta.get_field(field_name)
    names = list(
        queryset.exclude(**{field_name: ""})
        .exclude(**{f"{field_name}__isnull": True})
        .values_list(field_name, flat=True)
    )
    if not names:
        return 0
    queryset.update(**{field_name: blank_value, "updated_at": timezone.now()})
    return _schedule_file_deletion(field, names)


def anonymize_user_data(user, *, original_email: str, original_phone: str) -> dict[str, int]:
    from apps.accounts.models import OTPCode, StudentProfile
    from apps.audit.models import AuditLog
    from apps.chat.models import Message, MessageReport
    from apps.feedback.models import AppFeedback, FeedbackAITriage
    from apps.files.models import FileResource
    from apps.notifications.models import DeviceToken, Notification
    from apps.printing.models import (
        PrintOrder,
        PrintOrderItem,
        PrintOrderStatus,
        PrintOrderStatusHistory,
    )
    from apps.printing.services import PrintStatusService
    from apps.support.models import SupportTicket, SupportTicketMessage
    from apps.verification.models import VerificationRequest

    now = timezone.now()
    counts: dict[str, int] = {}

    # Student profile and verification requests.
    profiles = StudentProfile.objects.filter(user=user)
    counts["student_card_files"] = _clear_files(profiles, "card_image", blank_value=None)
    counts["student_profiles"] = profiles.update(
        student_number="",
        faculty_code_from_student_number="",
        enrollment_year_code="",
        enrollment_year_full=None,
        student_serial_number="",
        updated_at=now,
    )
    verifications = VerificationRequest.objects.filter(user=user)
    counts["verification_card_files"] = _clear_files(verifications, "card_image")
    counts["verification_requests"] = verifications.update(
        student_number="", rejection_reason="", admin_note="", updated_at=now
    )

    # Credentials / contact identifiers.
    otp_filter = models.Q(user=user)
    if original_email:
        otp_filter |= models.Q(email__iexact=original_email)
    if original_phone:
        otp_filter |= models.Q(phone_number=original_phone)
    counts["otp_codes"] = OTPCode.objects.filter(otp_filter).delete()[0]
    counts["device_tokens"] = DeviceToken.objects.filter(user=user).delete()[0]

    # Feedback.
    counts["feedback"] = AppFeedback.objects.filter(user=user).update(
        title="",
        comment="",
        suggestion="",
        internal_notes="",
        resolution_message="",
        metadata={},
        device_model="",
        journey_id="",
        session_id="",
        content_fingerprint="",
        updated_at=now,
    )
    counts["feedback_triage"] = FeedbackAITriage.objects.filter(feedback__user=user).update(
        redacted_text="", updated_at=now
    )

    # Support tickets (every message inside the user's own tickets may quote the user).
    messages = SupportTicketMessage.objects.filter(models.Q(ticket__user=user) | models.Q(sender=user))
    counts["support_attachment_files"] = _clear_files(messages, "attachment", blank_value=None)
    counts["support_messages"] = messages.update(message=REMOVED_TEXT, updated_at=now)
    counts["support_tickets"] = SupportTicket.objects.filter(user=user).update(subject=REMOVED_TEXT, updated_at=now)

    # Chat messages and reports written by the user.
    chat = Message.objects.filter(sender=user)
    counts["chat_attachment_files"] = _clear_files(chat, "attachment", blank_value=None)
    counts["chat_messages"] = chat.exclude(content="").update(content=REMOVED_TEXT, updated_at=now)
    counts["message_reports"] = MessageReport.objects.filter(reported_by=user).update(
        reason=REMOVED_TEXT, updated_at=now
    )

    # Uploaded library files owned by the user (rows kept: other rows reference them).
    resources = FileResource.objects.filter(uploaded_by=user)
    counts["uploaded_files"] = _clear_files(resources, "file")
    resources.update(title=REMOVED_TEXT, description="", sha256="", is_deleted=True, deleted_at=now)

    # Print orders: cancel open ones through the status machine, fall back to a direct update.
    terminal = {PrintOrderStatus.DELIVERED, PrintOrderStatus.CANCELLED, PrintOrderStatus.REJECTED}
    cancelled = 0
    for order in PrintOrder.objects.filter(user=user).exclude(status__in=terminal):
        try:
            with transaction.atomic():
                PrintStatusService.change_status(
                    order, PrintOrderStatus.CANCELLED, user, public_note="Account deleted."
                )
        except Exception:
            PrintOrder.objects.filter(pk=order.pk).update(
                status=PrintOrderStatus.CANCELLED, cancelled_at=now, updated_at=now
            )
        cancelled += 1
    counts["print_orders_cancelled"] = cancelled
    counts["print_orders"] = PrintOrder.objects.filter(user=user).update(user_notes="", updated_at=now)
    items = PrintOrderItem.objects.filter(order__user=user)
    counts["print_files"] = _clear_files(items, "uploaded_file", blank_value=None)
    items.exclude(original_file_name="").update(original_file_name=REMOVED_TEXT)
    PrintOrderStatusHistory.objects.filter(order__user=user).update(public_note="", internal_note="")

    # Notifications (after print cancellation, which creates one).
    counts["notifications"] = Notification.objects.filter(user=user).delete()[0]

    # Audit trail rows stay; network identifiers go.
    counts["audit_logs"] = AuditLog.objects.filter(actor=user).update(ip_address=None, user_agent="")
    return counts
