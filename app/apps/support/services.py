from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.notifications.models import NotificationType
from apps.notifications.services import NotificationService

from .models import SupportTicket, SupportTicketMessage, SupportTicketStatus


class SupportTicketService:
    @staticmethod
    @transaction.atomic
    def create_ticket(user, category: str, subject: str, message: str, attachment=None) -> SupportTicket:
        ticket = SupportTicket.objects.create(user=user, category=category, subject=subject)
        SupportTicketMessage.objects.create(ticket=ticket, sender=user, message=message, attachment=attachment)
        AuditLogService.log(actor=user, action=AuditAction.SUPPORT_TICKET_CREATED, target=ticket, new_value={"category": category})
        return ticket

    @staticmethod
    def add_message(ticket: SupportTicket, sender, message: str, attachment=None) -> SupportTicketMessage:
        if ticket.status in {SupportTicketStatus.RESOLVED, SupportTicketStatus.CLOSED}:
            raise ValidationError("Cannot add messages to a resolved or closed ticket.")
        ticket_message = SupportTicketMessage.objects.create(ticket=ticket, sender=sender, message=message, attachment=attachment)
        if sender_id := getattr(sender, "id", None):
            if sender_id != ticket.user_id:
                NotificationService.create_notification(
                    ticket.user,
                    "Support ticket updated",
                    f"A support team member replied to ticket #{ticket.id}.",
                    type=NotificationType.SYSTEM,
                    data={"support_ticket_id": ticket.id},
                )
                AuditLogService.log(
                    actor=sender,
                    action=AuditAction.SUPPORT_TICKET_STAFF_REPLY,
                    target=ticket,
                    new_value={"message_id": ticket_message.id},
                )
        return ticket_message

    @staticmethod
    @transaction.atomic
    def update_status(ticket: SupportTicket, status: str, actor) -> SupportTicket:
        old_status = ticket.status
        ticket.status = status
        ticket.close_if_needed()
        ticket.save()
        NotificationService.create_notification(
            ticket.user,
            "Support ticket status updated",
            f"Your ticket #{ticket.id} status changed to {status}.",
            type=NotificationType.SYSTEM,
            data={"support_ticket_id": ticket.id, "status": status},
        )
        AuditLogService.log(
            actor=actor,
            action=AuditAction.SUPPORT_TICKET_STATUS_CHANGED,
            target=ticket,
            old_value={"status": old_status},
            new_value={"status": status},
        )
        return ticket
