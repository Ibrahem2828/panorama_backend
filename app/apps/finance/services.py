from __future__ import annotations

import calendar
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Count, F, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.accounts.permissions import PermissionService
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.notifications.models import NotificationType
from apps.notifications.services import NotificationService
from apps.printing.models import PrintBinding, PrintColorMode, PrintOrder, PrintOrderItem, PrintOrderStatus

from .models import (
    ExchangeRate,
    Expense,
    FinanceCurrency,
    InventoryItem,
    OrderPayment,
    StockMovement,
    StockMovementType,
)

CENT = Decimal("0.01")


def money(value) -> Decimal:
    return Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP)


# ---- stock ------------------------------------------------------------------------------------------------------------------------


def _plain(quantity: Decimal) -> str:
    """12.50 -> "12.5", 4.00 -> "4": how a stock quantity reads in a message."""

    text = format(Decimal(quantity).quantize(Decimal("0.01")), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


class StockAlertService:
    """Tells the staff the moment an item runs low or runs out, once per crossing (not on every later movement).

    "Low" means the quantity is at or under the item's reorder level (a level of 0 switches the alert off). "Out" means the
    stock reached zero from above. Buying stock back over the level re-arms the alert, so the next dip warns again.
    """

    STAFF_ROLES = (UserRole.PRINT_STAFF, UserRole.ADMIN, UserRole.IT_SUPPORT)

    @staticmethod
    def kind(item: InventoryItem, before: Decimal, after: Decimal) -> str | None:
        if not item.is_active or item.is_deleted:
            return None
        if before > 0 and after <= 0:
            return "out_of_stock"
        level = item.reorder_level
        if level > 0 and before > level and after <= level:
            return "low_stock"
        return None

    @classmethod
    def recipients(cls) -> list[User]:
        candidates = User.objects.filter(role__in=cls.STAFF_ROLES, is_active=True, is_deleted=False)
        return [user for user in candidates if PermissionService.has(user, "printing.manage")]

    @classmethod
    def notify(cls, item: InventoryItem, before: Decimal, after: Decimal) -> str | None:
        kind = cls.kind(item, before, after)
        if kind is None:
            return None
        if kind == "out_of_stock":
            title = f"نفد المخزون: {item.name}"
            body = f"وصلت كمية «{item.name}» إلى الصفر. أعد التعبئة قبل أن يتوقف العمل."
        else:
            title = f"مخزون منخفض: {item.name}"
            body = (
                f"بقي {_plain(after)} {item.unit} من «{item.name}»، وحد إعادة الطلب {_plain(item.reorder_level)}. "
                "حان وقت الشراء."
            )
        data = {
            "kind": kind,
            "inventory_item_id": item.pk,
            "quantity": str(after),
            "reorder_level": str(item.reorder_level),
        }
        for user in cls.recipients():
            NotificationService.create_notification(user, title, body, type=NotificationType.PRINTING, data=data)
        return kind


class InventoryService:
    @staticmethod
    @transaction.atomic
    def record_movement(
        item: InventoryItem,
        movement_type: str,
        quantity: Decimal,
        *,
        actor=None,
        unit_cost: Decimal | None = None,
        currency: str = "",
        note: str = "",
        expense: Expense | None = None,
        order: PrintOrder | None = None,
    ) -> StockMovement:
        """Apply one stock movement under a row lock. Usage/waste cannot take the stock below zero.

        ``quantity`` is positive for purchase/usage/waste (the type decides the direction) and SIGNED for an adjustment.
        """

        quantity = Decimal(quantity)
        if quantity == 0:
            raise ValidationError({"quantity": "The quantity cannot be zero."})
        if movement_type != StockMovementType.ADJUSTMENT and quantity < 0:
            raise ValidationError({"quantity": "Use a positive quantity; the movement type decides the direction."})
        locked = InventoryItem.objects.select_for_update().get(pk=item.pk)
        before = locked.quantity
        delta = {
            StockMovementType.PURCHASE: quantity,
            StockMovementType.USAGE: -quantity,
            StockMovementType.WASTE: -quantity,
            StockMovementType.ADJUSTMENT: quantity,
        }[StockMovementType(movement_type)]
        after = locked.quantity + delta
        if after < 0:
            raise ValidationError({"quantity": f"Not enough stock: {locked.quantity} {locked.unit} available."})
        locked.quantity = after
        update_fields = ["quantity", "updated_at"]
        if movement_type == StockMovementType.PURCHASE and unit_cost is not None:
            locked.unit_cost = money(unit_cost)  # the last purchase price is the current valuation price
            if currency:
                locked.currency = currency
            update_fields += ["unit_cost", "currency"]
        locked.save(update_fields=update_fields)
        movement = StockMovement.objects.create(
            item=locked,
            movement_type=movement_type,
            quantity=delta,
            quantity_after=after,
            unit_cost=money(unit_cost) if unit_cost is not None else None,
            currency=currency or (locked.currency if unit_cost is not None else ""),
            note=note[:255],
            created_by=actor,
            expense=expense,
            order=order,
        )
        StockAlertService.notify(locked, before, after)
        return movement


# ---- automatic usage ---------------------------------------------------------------------------------------------------------------


_STOCK_BINDINGS = (PrintBinding.STAPLE, PrintBinding.SPIRAL, PrintBinding.THERMAL, PrintBinding.BEADS)
HUNDREDTH = Decimal("0.01")


class StockConsumptionService:
    """Takes from the shelf what a delivered print order used: paper by size and one binding unit per bound copy.

    Which stock item stands for which use is set on the item itself (``consumption_key`` + ``consumption_per_use``), so a shop that
    maps nothing deducts nothing. Ink is an estimate per printed page (the owner calibrates it to a bottle). An order is deducted exactly once
    (``stock_deducted_at``); running short never blocks the delivery, it is reported and the missing amount stays owed.
    """

    @staticmethod
    def uses(order: PrintOrder) -> dict[str, int]:
        """What the order used: sheets per paper size, bound copies per binding, printed pages for the inks, and the totals."""

        needs: dict[str, int] = defaultdict(int)
        pages = colour_pages = sheets = 0
        for item in order.items.filter(is_deleted=False):
            if item.sheets_count:
                needs[f"paper_{item.paper_size.lower()}"] += item.sheets_count
                sheets += item.sheets_count
            if item.binding in _STOCK_BINDINGS:
                needs[f"binding_{item.binding}"] += item.copies
            printed = (item.pages_count or 0) * item.copies  # every page of every copy is one printed side
            pages += printed
            if item.color_mode == PrintColorMode.COLOR:
                colour_pages += printed
        if pages:
            needs["ink_black"] = pages  # black is laid down on colour pages too
        if colour_pages:
            for ink in ("ink_cyan", "ink_magenta", "ink_yellow"):
                needs[ink] = colour_pages
        if sheets:
            needs["per_sheet"] = sheets
        needs["per_order"] = 1
        return dict(needs)

    @classmethod
    @transaction.atomic
    def deduct_for_order(cls, order: PrintOrder, actor=None, request=None) -> list[dict]:
        locked = PrintOrder.objects.select_for_update().get(pk=order.pk)
        if locked.stock_deducted_at:
            order.stock_deducted_at = locked.stock_deducted_at
            return []
        report: list[dict] = []
        for key, uses in sorted(cls.uses(locked).items()):
            item = (
                InventoryItem.objects.select_for_update()
                .filter(consumption_key=key, is_active=True, is_deleted=False, consumption_per_use__gt=0)
                .first()
            )
            if item is None:
                report.append({"key": key, "uses": uses, "item": None, "deducted": "0.00", "shortfall": "0.00"})
                continue
            owed = item.pending_consumption + Decimal(uses) * item.consumption_per_use
            whole = owed.quantize(HUNDREDTH, rounding=ROUND_DOWN)
            take = min(whole, item.quantity)
            shortfall = whole - take
            if take > 0:
                note = f"خصم تلقائي لطلب الطباعة #{locked.id}"
                if shortfall > 0:
                    note += f" (النقص {shortfall})"
                movement = InventoryService.record_movement(
                    item, StockMovementType.USAGE, take, actor=actor, note=note, order=locked
                )
                AuditLogService.log(
                    actor=actor,
                    action=AuditAction.STOCK_MOVEMENT_RECORDED,
                    target=item,
                    new_value={
                        "type": movement.movement_type,
                        "quantity": str(movement.quantity),
                        "after": str(movement.quantity_after),
                        "print_order": locked.id,
                        "automatic": True,
                        "shortfall": str(shortfall),
                    },
                    request=request,
                )
            # What is still owed (a sub-hundredth remainder, or stock that was missing) waits for the next order.
            InventoryItem.objects.filter(pk=item.pk).update(pending_consumption=owed - take)
            report.append(
                {"key": key, "uses": uses, "item": item.name, "deducted": str(take), "shortfall": str(shortfall)}
            )
        locked.stock_deducted_at = timezone.now()
        locked.save(update_fields=["stock_deducted_at", "updated_at"])
        order.stock_deducted_at = locked.stock_deducted_at
        return report


# ---- payments ---------------------------------------------------------------------------------------------------------------------


class PaymentService:
    @staticmethod
    def paid_amount(order: PrintOrder) -> Decimal:
        return money(order.payments.filter(is_deleted=False).aggregate(total=Sum("amount"))["total"])

    @classmethod
    @transaction.atomic
    def record(cls, order: PrintOrder, *, amount, method, actor=None, note: str = "", received_at=None) -> OrderPayment:
        order = PrintOrder.objects.select_for_update().get(pk=order.pk)
        if order.status in {PrintOrderStatus.CANCELLED, PrintOrderStatus.REJECTED}:
            raise ValidationError({"order": "A cancelled or rejected order cannot be paid."})
        amount = money(amount)
        outstanding = money(order.total_price) - cls.paid_amount(order)
        if amount > outstanding:
            raise ValidationError({"amount": f"The amount exceeds what is still due ({outstanding} {order.currency})."})
        return OrderPayment.objects.create(
            order=order,
            amount=amount,
            currency=order.currency,
            method=method,
            note=note[:255],
            received_by=actor,
            received_at=received_at or timezone.now(),
        )


# ---- reports ----------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Period:
    start: datetime
    end: datetime  # exclusive

    @classmethod
    def parse(cls, start: str | None, end: str | None) -> Period:
        """Calendar days in the server timezone; the end day is inclusive. Defaults to the current month."""

        today = timezone.localdate()
        try:
            first = date.fromisoformat(start) if start else today.replace(day=1)
            last = date.fromisoformat(end) if end else today
        except ValueError as exc:
            raise ValidationError({"from": "Dates must look like YYYY-MM-DD."}) from exc
        if last < first:
            raise ValidationError({"to": "The end date cannot be before the start date."})
        tz = timezone.get_current_timezone()
        return cls(
            start=timezone.make_aware(datetime.combine(first, time.min), tz),
            end=timezone.make_aware(datetime.combine(last + timedelta(days=1), time.min), tz),
        )


def finished_orders(period: Period):
    """Print orders that count as revenue: delivered to the customer, completed inside the period."""

    return PrintOrder.objects.filter(
        is_deleted=False,
        status=PrintOrderStatus.DELIVERED,
        completed_at__gte=period.start,
        completed_at__lt=period.end,
    )


def convert(amount: Decimal, source: str, target: str, rate: ExchangeRate | None) -> Decimal | None:
    if source == target:
        return money(amount)
    if rate is None:
        return None
    if source == FinanceCurrency.USD and target == FinanceCurrency.SYP:
        return money(amount * rate.syp_per_usd)
    if source == FinanceCurrency.SYP and target == FinanceCurrency.USD:
        return money(amount / rate.syp_per_usd)
    return None


def _total(bucket: dict[str, Decimal], target: str, rate: ExchangeRate | None) -> Decimal | None:
    parts = [convert(value, currency, target, rate) for currency, value in bucket.items()]
    known = [part for part in parts if part is not None]
    return None if len(known) != len(parts) else sum(known, Decimal("0"))


class StockReportService:
    """Consumption against purchases for one calendar month, item by item, computed from the movement ledger.

    The closing balance of the month is today's quantity minus every movement made after the month ended; the opening balance is the
    closing balance minus the month's own movements, so no snapshot has to be stored. "Used by orders" is the automatic deduction of
    delivered print orders; "used by hand" is every other usage movement.
    """

    #: A daily rate needs a few days of data before it is worth projecting from.
    MIN_DAYS_FOR_RATE = 3
    COVER_DAYS = 30

    @staticmethod
    def parse_month(value: str | None) -> tuple[date, date]:
        today = timezone.localdate()
        if value:
            match = re.fullmatch(r"(\d{4})-(\d{2})", value)
            if not match or not 1 <= int(match.group(2)) <= 12:
                raise ValidationError({"month": "Use a month like 2026-10."})
            first = date(int(match.group(1)), int(match.group(2)), 1)
        else:
            first = today.replace(day=1)
        if first > today:
            raise ValidationError({"month": "The month has not started yet."})
        last = date(first.year, first.month, calendar.monthrange(first.year, first.month)[1])
        return first, last

    @classmethod
    def monthly(cls, month: str | None) -> dict:
        first, last = cls.parse_month(month)
        period = Period.parse(first.isoformat(), last.isoformat())
        elapsed = (min(last, timezone.localdate()) - first).days + 1
        ledger = StockMovement.objects.filter(is_deleted=False)
        inside = ledger.filter(created_at__gte=period.start, created_at__lt=period.end)
        later = ledger.filter(created_at__gte=period.end)

        by_type: dict[int, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
        for row in inside.values("item", "movement_type").annotate(total=Sum("quantity")):
            by_type[row["item"]][row["movement_type"]] = row["total"]
        by_orders = {
            row["item"]: row["total"]
            for row in inside.filter(movement_type=StockMovementType.USAGE, order__isnull=False)
            .values("item")
            .annotate(total=Sum("quantity"))
        }
        after_period = {row["item"]: row["total"] for row in later.values("item").annotate(total=Sum("quantity"))}
        spent: dict[int, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
        purchases = inside.filter(movement_type=StockMovementType.PURCHASE, unit_cost__isnull=False)
        for row in purchases.values("item", "currency").annotate(cost=Sum(F("quantity") * F("unit_cost"))):
            spent[row["item"]][row["currency"]] += money(row["cost"])

        rows = []
        totals_cost: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        for item in InventoryItem.objects.filter(is_deleted=False).order_by("category", "name"):
            moves = by_type.get(item.pk, {})
            if not item.is_active and not moves:
                continue
            net = sum(moves.values(), Decimal("0"))
            closing = item.quantity - after_period.get(item.pk, Decimal("0"))
            opening = closing - net
            used = -moves.get(StockMovementType.USAGE, Decimal("0"))
            used_orders = -by_orders.get(item.pk, Decimal("0"))
            waste = -moves.get(StockMovementType.WASTE, Decimal("0"))
            daily = days_left = suggested = None
            if elapsed >= cls.MIN_DAYS_FOR_RATE and used > 0:
                rate = used / Decimal(elapsed)
                daily = rate.quantize(Decimal("0.0001"))
                days_left = int(closing / rate) if closing > 0 else 0
                missing = rate * cls.COVER_DAYS - closing
                suggested = Decimal(math.ceil(missing * 2) / 2).quantize(CENT) if missing > 0 else Decimal("0.00")
            for currency, amount in spent.get(item.pk, {}).items():
                totals_cost[currency] += amount
            rows.append(
                {
                    "id": item.pk,
                    "name": item.name,
                    "category": item.category,
                    "unit": item.unit,
                    "is_active": item.is_active,
                    "opening": opening.quantize(CENT),
                    "purchased": moves.get(StockMovementType.PURCHASE, Decimal("0")).quantize(CENT),
                    "purchase_cost": {currency: str(amount) for currency, amount in spent.get(item.pk, {}).items()},
                    "used": used.quantize(CENT),
                    "used_by_orders": used_orders.quantize(CENT),
                    "used_by_hand": (used - used_orders).quantize(CENT),
                    "waste": waste.quantize(CENT),
                    "adjustment": moves.get(StockMovementType.ADJUSTMENT, Decimal("0")).quantize(CENT),
                    "closing": closing.quantize(CENT),
                    "reorder_level": item.reorder_level,
                    "is_low": item.is_low,
                    "daily_usage": daily,
                    "days_left": days_left,
                    "suggested_purchase": suggested,
                }
            )
        delivered = finished_orders(period)
        sheets = PrintOrderItem.objects.filter(order__in=delivered).aggregate(sheets=Sum("sheets_count"))["sheets"] or 0
        return {
            "month": first.strftime("%Y-%m"),
            "period": {"from": first, "to": last},
            "days_elapsed": elapsed,
            "rate_basis_days": cls.MIN_DAYS_FOR_RATE,
            "cover_days": cls.COVER_DAYS,
            "items": rows,
            "totals": {
                "purchase_cost": {currency: str(amount) for currency, amount in totals_cost.items()},
                "orders_delivered": delivered.count(),
                "sheets_printed": sheets,
                "items_low": sum(1 for row in rows if row["is_low"]),
            },
        }


class FinanceReportService:
    @staticmethod
    def summary(period: Period, report_currency: str = FinanceCurrency.SYP) -> dict:
        rate = ExchangeRate.current()
        orders = finished_orders(period)
        revenue: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        for row in orders.values("currency").annotate(total=Sum("total_price")):
            revenue[row["currency"]] += money(row["total"])
        collected: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        payments = OrderPayment.objects.filter(is_deleted=False, order__in=orders)
        for row in payments.values("currency").annotate(total=Sum("amount")):
            collected[row["currency"]] += money(row["total"])
        expenses: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        by_category: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
        expense_rows = Expense.objects.filter(
            is_deleted=False, spent_on__gte=period.start.date(), spent_on__lt=period.end.date()
        )
        for row in expense_rows.values("currency", "category").annotate(total=Sum("amount")):
            expenses[row["currency"]] += money(row["total"])
            by_category[row["category"]][row["currency"]] += money(row["total"])

        revenue_total = _total(revenue, report_currency, rate)
        collected_total = _total(collected, report_currency, rate)
        expenses_total = _total(expenses, report_currency, rate)

        items = InventoryItem.objects.filter(is_deleted=False, is_active=True)
        stock_value: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        for item in items:
            stock_value[item.currency] += item.stock_value
        aggregates = PrintOrderItem.objects.filter(order__in=orders).aggregate(
            pages=Sum(F("pages_count") * F("copies")), sheets=Sum("sheets_count"), files=Count("id")
        )
        return {
            "period": {"from": period.start.date(), "to": (period.end - timedelta(days=1)).date()},
            "report_currency": report_currency,
            "exchange_rate": {"syp_per_usd": rate.syp_per_usd, "effective_from": rate.effective_from} if rate else None,
            "orders_count": orders.count(),
            "files_count": aggregates["files"] or 0,
            "pages_count": aggregates["pages"] or 0,
            "sheets_count": aggregates["sheets"] or 0,
            "revenue": dict(revenue),
            "collected": dict(collected),
            "expenses": dict(expenses),
            "expenses_by_category": {category: dict(values) for category, values in by_category.items()},
            "totals": {
                "revenue": revenue_total,
                "collected": collected_total,
                "expenses": expenses_total,
                "net": None if revenue_total is None or expenses_total is None else revenue_total - expenses_total,
                "outstanding": None
                if revenue_total is None or collected_total is None
                else revenue_total - collected_total,
            },
            "stock_value": dict(stock_value),
            "stock_value_total": _total(stock_value, report_currency, rate),
            "low_stock": [
                {
                    "id": i.id,
                    "name": i.name,
                    "category": i.category,
                    "quantity": i.quantity,
                    "unit": i.unit,
                    "reorder_level": i.reorder_level,
                }
                for i in items.filter(reorder_level__gt=0, quantity__lte=F("reorder_level")).order_by("quantity")
            ],
        }
