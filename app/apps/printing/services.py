from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.files.services import user_can_access_file
from apps.notifications.models import NotificationType
from apps.notifications.services import NotificationService

from .models import PrintOrder, PrintOrderItem, PrintOrderPriority, PrintOrderStatus, PrintOrderStatusHistory


VALID_TRANSITIONS = {
    PrintOrderStatus.SUBMITTED: {PrintOrderStatus.UNDER_REVIEW, PrintOrderStatus.CANCELLED, PrintOrderStatus.REJECTED},
    PrintOrderStatus.UNDER_REVIEW: {PrintOrderStatus.ACCEPTED, PrintOrderStatus.CANCELLED, PrintOrderStatus.REJECTED},
    PrintOrderStatus.ACCEPTED: {PrintOrderStatus.PRINTING, PrintOrderStatus.CANCELLED},
    PrintOrderStatus.PRINTING: {PrintOrderStatus.READY},
    PrintOrderStatus.READY: {PrintOrderStatus.DELIVERED},
}


def default_priority_for_user(user) -> str:
    profile = getattr(user, "student_profile", None)
    if user.role == UserRole.STUDENT and profile and profile.verification_status == StudentVerificationStatus.APPROVED:
        return PrintOrderPriority.STUDENT_PRIORITY
    return PrintOrderPriority.NORMAL


class PrintOrderService:
    @staticmethod
    @transaction.atomic
    def create_order(user, items_data: list[dict], user_notes: str = "") -> PrintOrder:
        if not items_data:
            raise ValidationError({"items": "At least one print item is required."})
        order = PrintOrder.objects.create(user=user, priority=default_priority_for_user(user), user_notes=user_notes)
        total = Decimal("0")
        for item_data in items_data:
            source_file = item_data.get("source_file")
            if source_file:
                if not user_can_access_file(user, source_file):
                    raise ValidationError({"source_file": "You do not have access to this file."})
                if not source_file.is_printable:
                    raise ValidationError({"source_file": "This file is not printable."})
            item = PrintOrderItem.objects.create(order=order, **item_data)
            total += item.price
        order.total_price = total
        order.save(update_fields=["total_price", "updated_at"])
        NotificationService.create_notification(
            user,
            "Print order submitted",
            f"Your print order #{order.id} was submitted.",
            type=NotificationType.SYSTEM,
            data={"print_order_id": order.id, "status": order.status},
        )
        AuditLogService.log(actor=user, action=AuditAction.PRINT_ORDER_CREATED, target=order, new_value={"status": order.status})
        return order


class PrintStatusService:
    @staticmethod
    @transaction.atomic
    def change_status(order: PrintOrder, new_status: str, changed_by, note: str = "", rejected_reason: str = "") -> PrintOrder:
        order = PrintOrder.objects.select_for_update().select_related("user").get(pk=order.pk, is_deleted=False)
        allowed = VALID_TRANSITIONS.get(order.status, set())
        if new_status not in allowed:
            raise ValidationError({"status": f"Invalid transition from {order.status} to {new_status}."})
        old_status = order.status
        order.status = new_status
        if new_status == PrintOrderStatus.DELIVERED:
            order.completed_at = timezone.now()
        if new_status == PrintOrderStatus.CANCELLED:
            order.cancelled_at = timezone.now()
        if new_status == PrintOrderStatus.REJECTED:
            order.rejected_reason = rejected_reason or note
        order.save()
        PrintOrderStatusHistory.objects.create(order=order, old_status=old_status, new_status=new_status, changed_by=changed_by, note=note)
        NotificationService.create_notification(
            order.user,
            "Print order status updated",
            f"Your print order #{order.id} status changed to {new_status}.",
            type=NotificationType.SYSTEM,
            data={"print_order_id": order.id, "status": new_status},
        )
        AuditLogService.log(
            actor=changed_by,
            action=AuditAction.PRINT_ORDER_STATUS_CHANGED,
            target=order,
            old_value={"status": old_status},
            new_value={"status": new_status},
        )
        return order
