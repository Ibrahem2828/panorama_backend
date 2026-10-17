from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Count, F, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.printing.models import PrintOrder, PrintOrderItem, PrintOrderStatus

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
        return StockMovement.objects.create(
            item=locked,
            movement_type=movement_type,
            quantity=delta,
            quantity_after=after,
            unit_cost=money(unit_cost) if unit_cost is not None else None,
            currency=currency or (locked.currency if unit_cost is not None else ""),
            note=note[:255],
            created_by=actor,
            expense=expense,
        )


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
