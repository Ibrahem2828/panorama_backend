from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import ceil

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.files.document_inspection import detect_pages_count
from apps.files.services import user_can_access_file
from apps.notifications.models import NotificationType
from apps.notifications.services import NotificationService

from .models import (
    PrintBinding,
    PrintBindingPrice,
    PrintOrder,
    PrintOrderItem,
    PrintOrderPriority,
    PrintOrderStatus,
    PrintOrderStatusHistory,
    PrintPricingRule,
    PrintSides,
)


VALID_TRANSITIONS = {
    PrintOrderStatus.SUBMITTED: {PrintOrderStatus.UNDER_REVIEW, PrintOrderStatus.CANCELLED, PrintOrderStatus.REJECTED},
    PrintOrderStatus.UNDER_REVIEW: {PrintOrderStatus.ACCEPTED, PrintOrderStatus.CANCELLED, PrintOrderStatus.REJECTED},
    PrintOrderStatus.ACCEPTED: {PrintOrderStatus.PRINTING, PrintOrderStatus.CANCELLED},
    PrintOrderStatus.PRINTING: {PrintOrderStatus.READY},
    PrintOrderStatus.READY: {PrintOrderStatus.DELIVERED},
}


@dataclass(frozen=True)
class PricedItem:
    input_data: dict
    pages_count: int
    sheets_count: int
    unit_price: Decimal
    binding_price: Decimal
    subtotal: Decimal
    currency: str
    snapshot: dict


def default_priority_for_user(user) -> str:
    profile = getattr(user, "student_profile", None)
    if user.role == UserRole.STUDENT and profile and profile.verification_status == StudentVerificationStatus.APPROVED:
        return PrintOrderPriority.STUDENT_PRIORITY
    return PrintOrderPriority.NORMAL


class PrintPricingService:
    @staticmethod
    def _active_rule(item_data: dict) -> PrintPricingRule:
        now = timezone.now()
        rule = (
            PrintPricingRule.objects.filter(
                color_mode=item_data["color_mode"],
                paper_size=item_data["paper_size"],
                sides=item_data["sides"],
                is_active=True,
                is_deleted=False,
                effective_from__lte=now,
            )
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=now))
            .order_by("-effective_from", "-id")
            .first()
        )
        if not rule:
            raise ValidationError({"items": "No active pricing rule matches the selected print options."})
        return rule

    @staticmethod
    def _binding(item_data: dict, currency: str) -> PrintBindingPrice | None:
        if item_data["binding"] == PrintBinding.NONE:
            return None
        binding = PrintBindingPrice.objects.filter(
            binding=item_data["binding"],
            is_active=True,
            is_deleted=False,
        ).first()
        if not binding:
            raise ValidationError({"items": "The selected binding service is currently unavailable."})
        if binding.currency != currency:
            raise ValidationError({"items": "Pricing configuration uses inconsistent currencies."})
        return binding

    @staticmethod
    def _pages_count(item_data: dict) -> int:
        source_file = item_data.get("source_file")
        uploaded_file = item_data.get("uploaded_file")
        if source_file:
            if source_file.pages_count:
                return source_file.pages_count
            source_file.file.open("rb")
            try:
                return detect_pages_count(source_file.file, source_file.file.name)
            finally:
                source_file.file.close()
        if uploaded_file:
            return detect_pages_count(uploaded_file, uploaded_file.name)
        raise ValidationError({"items": "A source file or uploaded file is required."})

    @classmethod
    def price_item(cls, item_data: dict) -> PricedItem:
        pages_count = cls._pages_count(item_data)
        copies = int(item_data["copies"])
        if not 1 <= copies <= 99:
            raise ValidationError({"copies": "Copies must be between 1 and 99."})
        rule = cls._active_rule(item_data)
        binding_rule = cls._binding(item_data, rule.currency)
        sheets_per_copy = ceil(pages_count / 2) if item_data["sides"] == PrintSides.DOUBLE_SIDED else pages_count
        sheets_count = sheets_per_copy * copies
        print_cost = (Decimal(sheets_count) * rule.price_per_sheet) + rule.setup_fee
        binding_price = binding_rule.price_per_copy if binding_rule else Decimal("0")
        binding_cost = binding_price * copies
        subtotal = print_cost + binding_cost
        snapshot = {
            "pricing_rule_id": rule.id,
            "pricing_rule_name": rule.name,
            "price_per_sheet": str(rule.price_per_sheet),
            "setup_fee": str(rule.setup_fee),
            "binding_rule_id": binding_rule.id if binding_rule else None,
            "binding_price_per_copy": str(binding_price),
            "pages_count": pages_count,
            "sheets_per_copy": sheets_per_copy,
            "copies": copies,
            "total_sheets": sheets_count,
            "color_mode": item_data["color_mode"],
            "paper_size": item_data["paper_size"],
            "sides": item_data["sides"],
            "binding": item_data["binding"],
            "currency": rule.currency,
            "subtotal": str(subtotal),
        }
        return PricedItem(
            input_data=item_data,
            pages_count=pages_count,
            sheets_count=sheets_count,
            unit_price=rule.price_per_sheet,
            binding_price=binding_price,
            subtotal=subtotal,
            currency=rule.currency,
            snapshot=snapshot,
        )

    @classmethod
    def quote(cls, user, items_data: list[dict]) -> dict:
        if not items_data:
            raise ValidationError({"items": "At least one print item is required."})
        priced_items: list[PricedItem] = []
        for item_data in items_data:
            source_file = item_data.get("source_file")
            if source_file:
                if not user_can_access_file(user, source_file):
                    raise ValidationError({"source_file": "You do not have access to this file."})
                if not source_file.is_printable:
                    raise ValidationError({"source_file": "This file is not printable."})
            priced_items.append(cls.price_item(item_data))
        currencies = {item.currency for item in priced_items}
        if len(currencies) != 1:
            raise ValidationError({"items": "All quoted items must use the same currency."})
        total = sum((item.subtotal for item in priced_items), Decimal("0"))
        currency = currencies.pop() if currencies else getattr(settings, "PRINT_CURRENCY", "SYP")
        return {
            "total_price": total,
            "currency": currency,
            "items": priced_items,
            "calculated_at": timezone.now(),
        }


class PrintOrderService:
    @staticmethod
    @transaction.atomic
    def create_order(user, items_data: list[dict], user_notes: str = "", pickup_location=None, request=None) -> PrintOrder:
        quote = PrintPricingService.quote(user, items_data)
        order = PrintOrder.objects.create(
            user=user,
            priority=default_priority_for_user(user),
            user_notes=user_notes,
            pickup_location=pickup_location,
            total_price=quote["total_price"],
            currency=quote["currency"],
            price_calculated_at=quote["calculated_at"],
            pricing_snapshot={
                "currency": quote["currency"],
                "total_price": str(quote["total_price"]),
                "calculated_at": quote["calculated_at"].isoformat(),
            },
        )
        for priced in quote["items"]:
            data = dict(priced.input_data)
            data.pop("pages_count", None)
            data.pop("price", None)
            PrintOrderItem.objects.create(
                order=order,
                **data,
                pages_count=priced.pages_count,
                sheets_count=priced.sheets_count,
                unit_price=priced.unit_price,
                binding_price=priced.binding_price,
                price=priced.subtotal,
                pricing_snapshot=priced.snapshot,
            )
        PrintOrderStatusHistory.objects.create(
            order=order,
            old_status="",
            new_status=PrintOrderStatus.SUBMITTED,
            changed_by=user,
            public_note="تم استلام الطلب بنجاح.",
            internal_note="Order created",
        )
        NotificationService.create_notification(
            user,
            "تم إرسال طلب الطباعة",
            f"تم استلام طلب الطباعة رقم {order.id} بقيمة {order.total_price} {order.currency}.",
            type=NotificationType.PRINTING,
            data={"print_order_id": order.id, "status": order.status},
        )
        AuditLogService.log(
            actor=user,
            action=AuditAction.PRINT_ORDER_CREATED,
            target=order,
            new_value={"status": order.status, "total_price": str(order.total_price), "currency": order.currency},
            request=request,
        )
        return order


class PrintStatusService:
    @staticmethod
    @transaction.atomic
    def change_status(
        order: PrintOrder,
        new_status: str,
        changed_by,
        public_note: str = "",
        internal_note: str = "",
        rejected_reason: str = "",
        request=None,
    ) -> PrintOrder:
        order = PrintOrder.objects.select_for_update().get(pk=order.pk, is_deleted=False)
        allowed = VALID_TRANSITIONS.get(order.status, set())
        if new_status not in allowed:
            raise ValidationError({"status": f"Invalid transition from {order.status} to {new_status}."})
        if new_status == PrintOrderStatus.REJECTED and not (rejected_reason or public_note or internal_note).strip():
            raise ValidationError({"rejected_reason": "A rejection reason is required."})
        old_status = order.status
        order.status = new_status
        if new_status == PrintOrderStatus.DELIVERED:
            order.completed_at = timezone.now()
        if new_status == PrintOrderStatus.CANCELLED:
            order.cancelled_at = timezone.now()
        if new_status == PrintOrderStatus.REJECTED:
            order.rejected_reason = (rejected_reason or public_note).strip()
        order.save()
        PrintOrderStatusHistory.objects.create(
            order=order,
            old_status=old_status,
            new_status=new_status,
            changed_by=changed_by,
            public_note=public_note.strip(),
            internal_note=internal_note.strip(),
        )
        NotificationService.create_notification(
            order.user,
            "تم تحديث حالة طلب الطباعة",
            f"أصبحت حالة طلب الطباعة رقم {order.id}: {new_status}.",
            type=NotificationType.PRINTING,
            data={"print_order_id": order.id, "status": new_status},
        )
        AuditLogService.log(
            actor=changed_by,
            action=AuditAction.PRINT_ORDER_STATUS_CHANGED,
            target=order,
            old_value={"status": old_status},
            new_value={"status": new_status},
            request=request,
        )
        return order
