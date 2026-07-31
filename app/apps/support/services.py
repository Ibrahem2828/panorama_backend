from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.permissions import Capability, PermissionService
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.notifications.models import NotificationType
from apps.notifications.services import NotificationService

from .models import SupportAttachmentAccessTicket, SupportTicket, SupportTicketMessage, SupportTicketStatus


class SupportTicketService:
    VALID_TRANSITIONS = {
        SupportTicketStatus.OPEN: {
            SupportTicketStatus.IN_PROGRESS,
            SupportTicketStatus.WAITING_USER,
            SupportTicketStatus.RESOLVED,
            SupportTicketStatus.CLOSED,
        },
        SupportTicketStatus.IN_PROGRESS: {
            SupportTicketStatus.WAITING_USER,
            SupportTicketStatus.RESOLVED,
            SupportTicketStatus.CLOSED,
        },
        SupportTicketStatus.WAITING_USER: {
            SupportTicketStatus.IN_PROGRESS,
            SupportTicketStatus.RESOLVED,
            SupportTicketStatus.CLOSED,
        },
        SupportTicketStatus.RESOLVED: {SupportTicketStatus.IN_PROGRESS, SupportTicketStatus.CLOSED},
        SupportTicketStatus.CLOSED: {SupportTicketStatus.IN_PROGRESS},
    }

    @staticmethod
    @transaction.atomic
    def create_ticket(user, category: str, subject: str, message: str, attachment=None, request=None) -> SupportTicket:
        ticket = SupportTicket.objects.create(user=user, category=category, subject=subject.strip())
        SupportTicketMessage.objects.create(ticket=ticket, sender=user, message=message.strip(), attachment=attachment)
        AuditLogService.log(
            actor=user,
            action=AuditAction.SUPPORT_TICKET_CREATED,
            target=ticket,
            new_value={"category": category},
            request=request,
        )
        return ticket

    @staticmethod
    @transaction.atomic
    def add_message(ticket: SupportTicket, sender, message: str, attachment=None) -> SupportTicketMessage:
        ticket = SupportTicket.objects.select_for_update().get(pk=ticket.pk)
        staff = PermissionService.has(sender, Capability.SUPPORT_MANAGE)
        if ticket.status == SupportTicketStatus.CLOSED:
            raise ValidationError({"ticket": "Closed tickets must be reopened before adding a message."})
        if ticket.status == SupportTicketStatus.RESOLVED and not staff:
            raise ValidationError({"ticket": "Resolved tickets cannot receive user replies until reopened."})
        ticket_message = SupportTicketMessage.objects.create(
            ticket=ticket,
            sender=sender,
            message=message.strip(),
            attachment=attachment,
        )
        ticket.last_response_at = timezone.now()
        if staff and ticket.status in {SupportTicketStatus.OPEN, SupportTicketStatus.WAITING_USER}:
            ticket.status = SupportTicketStatus.WAITING_USER
        elif not staff and ticket.status == SupportTicketStatus.WAITING_USER:
            ticket.status = SupportTicketStatus.IN_PROGRESS
        ticket.save(update_fields=["last_response_at", "status", "updated_at"])
        if sender.id != ticket.user_id:
            NotificationService.create_notification(
                ticket.user,
                "تم تحديث تذكرة الدعم",
                f"قام فريق الدعم بالرد على التذكرة رقم {ticket.id}.",
                type=NotificationType.SUPPORT,
                data={"support_ticket_id": ticket.id},
            )
        return ticket_message

    @classmethod
    @transaction.atomic
    def update_status(cls, ticket: SupportTicket, new_status: str, actor, request=None) -> SupportTicket:
        ticket = SupportTicket.objects.select_for_update().get(pk=ticket.pk)
        old_status = ticket.status
        if new_status == old_status:
            return ticket
        if new_status not in cls.VALID_TRANSITIONS.get(old_status, set()):
            raise ValidationError({"status": f"Invalid status transition from {old_status} to {new_status}."})
        ticket.status = new_status
        ticket.close_if_needed()
        ticket.save(update_fields=["status", "closed_at", "updated_at"])
        NotificationService.create_notification(
            ticket.user,
            "تم تحديث حالة تذكرة الدعم",
            f"تم تغيير حالة التذكرة رقم {ticket.id} إلى {new_status}.",
            type=NotificationType.SUPPORT,
            data={"support_ticket_id": ticket.id, "status": new_status},
        )
        AuditLogService.log(
            actor=actor,
            action=AuditAction.SUPPORT_TICKET_STATUS_CHANGED,
            target=ticket,
            old_value={"status": old_status},
            new_value={"status": new_status},
            request=request,
        )
        return ticket

    @staticmethod
    def can_access_ticket(user, ticket: SupportTicket) -> bool:
        return user.id == ticket.user_id or PermissionService.has(user, Capability.SUPPORT_MANAGE)

    @classmethod
    def issue_attachment_ticket(cls, message: SupportTicketMessage, user):
        if not message.attachment:
            raise ValidationError({"attachment": "This message has no attachment."})
        if not cls.can_access_ticket(user, message.ticket):
            raise PermissionDenied("You cannot access this support attachment.")
        return SupportAttachmentAccessTicket.issue(message=message, requested_by=user)
