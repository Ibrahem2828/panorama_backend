from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from math import ceil

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.exceptions import IdempotencyKeyReused
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
    PrintOrderSource,
    PrintOrderStatus,
    PrintOrderStatusHistory,
    PrintPricingRule,
    PrintSides,
)


class PrintPricingUnavailable(APIException):
    """No active price for the chosen options: a configuration gap, not a mistake by the user."""

    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Printing prices are not available for the selected options yet."
    default_code = "print_pricing_unavailable"
    api_code = "PRINT_PRICING_UNAVAILABLE"


class PrintBindingUnavailable(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "The selected binding service is currently unavailable."
    default_code = "print_binding_unavailable"
    api_code = "PRINT_BINDING_UNAVAILABLE"


PRINT_CURRENCIES = ("SYP", "USD")


def default_currency() -> str:
    """The currency used when a client does not choose one: the NEW Syrian pound unless configured otherwise."""

    configured = getattr(settings, "PRINT_CURRENCY", "SYP")
    return configured if configured in PRINT_CURRENCIES else "SYP"


CENT = Decimal("0.01")
POUND = Decimal("1")


def round_syp(amount: Decimal) -> Decimal:
    """Round a new-Syrian-pound price the way the print shop quotes it: to a whole ten.

    The last digit decides: 0 stays, 1-2 round DOWN (151, 152 -> 150), 3-9 round UP (153, 154, 156 -> 160). A positive price
    never becomes zero: 1-2 pounds quote as 10. Fractions of a pound are first rounded to the nearest pound.
    """

    whole = int(Decimal(amount).quantize(POUND, rounding=ROUND_HALF_UP))
    if whole <= 0:
        return Decimal("0.00")
    tens, last = divmod(whole, 10)
    if last == 0:
        rounded = whole
    elif last <= 2:
        rounded = max(tens * 10, 10)
    else:
        rounded = (tens + 1) * 10
    return Decimal(rounded).quantize(CENT)


def round_price(amount: Decimal, currency: str) -> Decimal:
    """The price shown to the customer: SYP is rounded to tens, any other currency keeps its cents."""

    if currency == "SYP":
        return round_syp(amount)
    return Decimal(amount).quantize(CENT, rounding=ROUND_HALF_UP)


VALID_TRANSITIONS = {
    PrintOrderStatus.SUBMITTED: {PrintOrderStatus.UNDER_REVIEW, PrintOrderStatus.CANCELLED, PrintOrderStatus.REJECTED},
    PrintOrderStatus.UNDER_REVIEW: {PrintOrderStatus.ACCEPTED, PrintOrderStatus.CANCELLED, PrintOrderStatus.REJECTED},
    PrintOrderStatus.ACCEPTED: {PrintOrderStatus.PRINTING, PrintOrderStatus.CANCELLED},
    PrintOrderStatus.PRINTING: {PrintOrderStatus.READY},
    PrintOrderStatus.READY: {PrintOrderStatus.DELIVERED},
}


def _request_fingerprint(items_data: list[dict], user_notes: str, pickup_location, currency: str = "") -> str:
    """Stable digest of what the user asked for, so a reused Idempotency-Key with a different body is detectable."""

    def source(item: dict):
        if item.get("uploaded_file"):
            uploaded = item["uploaded_file"]
            return ["upload", getattr(uploaded, "name", ""), getattr(uploaded, "size", 0)]
        source_file = item.get("source_file")
        return ["file", getattr(source_file, "pk", source_file)]

    canonical = [
        [
            source(item),
            item.get("copies"),
            str(item.get("color_mode")),
            str(item.get("paper_size")),
            str(item.get("sides")),
            str(item.get("binding")),
            str(item.get("page_range") or ""),
        ]
        for item in items_data
    ]
    payload = json.dumps(
        [canonical, user_notes or "", getattr(pickup_location, "pk", None)], sort_keys=True, default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
    def pricing_revision(priced_items: list[PricedItem]) -> str:
        """Stable server-calculated revision of every rule used by a quote."""

        payload = [item.snapshot for item in priced_items]
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _active_rule(item_data: dict, currency: str) -> PrintPricingRule:
        now = timezone.now()
        rule = (
            PrintPricingRule.objects.filter(
                currency=currency,
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
            raise PrintPricingUnavailable()
        return rule

    @staticmethod
    def _binding(item_data: dict, currency: str) -> PrintBindingPrice | None:
        if item_data["binding"] == PrintBinding.NONE:
            return None
        binding = PrintBindingPrice.objects.filter(
            binding=item_data["binding"],
            currency=currency,
            is_active=True,
            is_deleted=False,
        ).first()
        if not binding:
            raise PrintBindingUnavailable()
        return binding

    @staticmethod
    def _pages_count(item_data: dict) -> int:
        manual = item_data.get("manual_pages_count")  # typed in at the print shop counter: there is no file to read
        if manual:
            return int(manual)
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
    def price_item(cls, item_data: dict, currency: str) -> PricedItem:
        pages_count = cls._pages_count(item_data)
        copies = int(item_data["copies"])
        if not 1 <= copies <= 99:
            raise ValidationError({"copies": "Copies must be between 1 and 99."})
        rule = cls._active_rule(item_data, currency)
        binding_rule = cls._binding(item_data, currency)
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
    def quote(cls, user, items_data: list[dict], currency: str | None = None) -> dict:
        currency = currency or default_currency()
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
            priced_items.append(cls.price_item(item_data, currency))
        exact_total = sum((item.subtotal for item in priced_items), Decimal("0"))
        total = round_price(exact_total, currency)
        return {
            "total_price": total,
            "exact_total": exact_total.quantize(CENT, rounding=ROUND_HALF_UP),
            "rounding_adjustment": total - exact_total.quantize(CENT, rounding=ROUND_HALF_UP),
            "currency": currency,
            "items": priced_items,
            "pricing_revision": cls.pricing_revision(priced_items),
            "calculated_at": timezone.now(),
        }


class PrintOrderService:
    @staticmethod
    @transaction.atomic
    def create_order(
        user,
        items_data: list[dict],
        user_notes: str = "",
        pickup_location=None,
        request=None,
        idempotency_key: str = "",
        currency: str | None = None,
    ) -> PrintOrder:
        user = user.__class__.objects.select_for_update().get(pk=user.pk)
        key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest() if idempotency_key else ""
        currency = currency or default_currency()
        fingerprint = _request_fingerprint(items_data, user_notes, pickup_location, currency)
        if key_hash:
            existing = (
                PrintOrder.objects.select_for_update()
                .filter(
                    user=user,
                    idempotency_key_hash=key_hash,
                    is_deleted=False,
                )
                .first()
            )
            if existing:
                stored = (existing.pricing_snapshot or {}).get("request_fingerprint")
                # Orders created before fingerprints existed have none: replay them as before.
                if stored and stored != fingerprint:
                    raise IdempotencyKeyReused()
                return existing
        quote = PrintPricingService.quote(user, items_data, currency)
        order = PrintOrder.objects.create(
            user=user,
            idempotency_key_hash=key_hash,
            priority=default_priority_for_user(user),
            user_notes=user_notes,
            pickup_location=pickup_location,
            total_price=quote["total_price"],
            currency=quote["currency"],
            price_calculated_at=quote["calculated_at"],
            pricing_revision=quote["pricing_revision"],
            pricing_snapshot={
                "currency": quote["currency"],
                "total_price": str(quote["total_price"]),
                "exact_total": str(quote["exact_total"]),
                "rounding_adjustment": str(quote["rounding_adjustment"]),
                "pricing_revision": quote["pricing_revision"],
                "calculated_at": quote["calculated_at"].isoformat(),
                "request_fingerprint": fingerprint,
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
        allowed = VALID_TRANSITIONS.get(PrintOrderStatus(order.status), set())
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
        if order.user_id:  # a counter order has no account to notify
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


COUNTER_STATUSES = (PrintOrderStatus.PRINTING, PrintOrderStatus.READY, PrintOrderStatus.DELIVERED)
_STAFF_ROLES = (UserRole.PRINT_STAFF, UserRole.ADMIN, UserRole.IT_SUPPORT)


class CounterOrderService:
    """Print orders typed in by the print-shop staff for a customer standing at the counter (no app, no account)."""

    @staticmethod
    def quote(items_data: list[dict], currency: str | None = None) -> dict:
        """Price every line with the shop's own rules. A line the rules cannot price is reported, never guessed."""

        currency = currency or default_currency()
        lines: list[dict] = []
        exact = Decimal("0")
        complete = True
        for item in items_data:
            data = {**item, "manual_pages_count": item["pages_count"]}
            try:
                priced = PrintPricingService.price_item(data, currency)
            except (PrintPricingUnavailable, PrintBindingUnavailable) as error:
                complete = False
                pages = int(item["pages_count"])
                copies = int(item["copies"])
                per_copy = ceil(pages / 2) if item["sides"] == PrintSides.DOUBLE_SIDED else pages
                lines.append(
                    {
                        "pages_count": pages,
                        "sheets_count": per_copy * copies,
                        "unit_price": None,
                        "binding_price": None,
                        "subtotal": None,
                        "unavailable": "binding" if isinstance(error, PrintBindingUnavailable) else "pricing",
                    }
                )
                continue
            exact += priced.subtotal
            lines.append(
                {
                    "pages_count": priced.pages_count,
                    "sheets_count": priced.sheets_count,
                    "unit_price": priced.unit_price,
                    "binding_price": priced.binding_price,
                    "subtotal": priced.subtotal,
                    "unavailable": None,
                    "snapshot": priced.snapshot,
                }
            )
        exact = exact.quantize(CENT, rounding=ROUND_HALF_UP)
        suggested = round_price(exact, currency) if complete else None
        return {
            "currency": currency,
            "items": lines,
            "complete": complete,
            "exact_total": exact,
            "suggested_total": suggested,
            "rounding_adjustment": (suggested - exact) if suggested is not None else None,
        }

    @classmethod
    @transaction.atomic
    def create(
        cls,
        actor,
        items_data: list[dict],
        *,
        customer_name: str = "",
        customer_phone: str = "",
        currency: str | None = None,
        total_price: Decimal | None = None,
        status_value: str = PrintOrderStatus.DELIVERED,
        paid_amount: Decimal | None = None,
        payment_method: str = "cash",
        notes: str = "",
        internal_notes: str = "",
        request=None,
    ) -> PrintOrder:
        from apps.finance.services import PaymentService, money

        if status_value not in COUNTER_STATUSES:
            raise ValidationError({"status": "A counter order starts as printing, ready or delivered."})
        quote = cls.quote(items_data, currency)
        currency = quote["currency"]
        if total_price is None:
            if not quote["complete"]:
                raise ValidationError({"total_price": "Enter the price: some lines have no price in the price list."})
            charged = quote["suggested_total"]
        else:
            charged = money(total_price)
        if paid_amount is None:
            paid_amount = charged if status_value == PrintOrderStatus.DELIVERED else Decimal("0")
        paid_amount = money(paid_amount)
        if paid_amount > charged:
            raise ValidationError({"paid_amount": "The amount received cannot exceed the price."})
        now = timezone.now()
        # The rounding belongs to the list price. If the cashier charges another amount, nothing was rounded.
        overridden = quote["suggested_total"] is None or charged != quote["suggested_total"]
        rounding = Decimal("0.00") if overridden else (quote["rounding_adjustment"] or Decimal("0.00"))
        order = PrintOrder.objects.create(
            user=None,
            source=PrintOrderSource.COUNTER,
            customer_name=customer_name.strip(),
            customer_phone=customer_phone.strip(),
            created_by=actor,
            status=status_value,
            priority=PrintOrderPriority.NORMAL,
            total_price=charged,
            currency=currency,
            price_calculated_at=now,
            user_notes=notes.strip(),
            internal_notes=internal_notes.strip(),
            assigned_to=actor if actor.role in _STAFF_ROLES else None,
            completed_at=now if status_value == PrintOrderStatus.DELIVERED else None,
            pricing_snapshot={
                "counter": True,
                "currency": currency,
                "total_price": str(charged),
                "exact_total": str(quote["exact_total"]),
                "suggested_total": str(quote["suggested_total"]) if quote["suggested_total"] is not None else None,
                "rounding_adjustment": str(rounding),
                "price_overridden": overridden,
                "calculated_at": now.isoformat(),
            },
        )
        for item, line in zip(items_data, quote["items"], strict=True):
            PrintOrderItem.objects.create(
                order=order,
                original_file_name=item.get("title", "")[:255],
                pages_count=line["pages_count"],
                copies=item["copies"],
                color_mode=item["color_mode"],
                paper_size=item["paper_size"],
                sides=item["sides"],
                binding=item["binding"],
                binding_note=item.get("binding_note", "")[:120],
                sheets_count=line["sheets_count"],
                unit_price=line["unit_price"] or Decimal("0"),
                binding_price=line["binding_price"] or Decimal("0"),
                price=line["subtotal"] or Decimal("0"),
                pricing_snapshot=line.get("snapshot") or {"unavailable": line["unavailable"]},
            )
        PrintOrderStatusHistory.objects.create(
            order=order,
            old_status="",
            new_status=status_value,
            changed_by=actor,
            public_note="طلب من المحل.",
            internal_note="Counter order",
        )
        if paid_amount > 0:
            PaymentService.record(
                order, amount=paid_amount, method=payment_method, actor=actor, note="Received at the counter"
            )
        AuditLogService.log(
            actor=actor,
            action=AuditAction.PRINT_ORDER_CREATED,
            target=order,
            new_value={
                "counter": True,
                "status": order.status,
                "total_price": str(order.total_price),
                "currency": order.currency,
                "paid": str(paid_amount),
            },
            request=request,
        )
        return order
