from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Sum
from rest_framework.exceptions import ValidationError

from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.finance.models import InventoryItem, StockMovementType
from apps.finance.services import InventoryService, Period, money

from .models import StationerySale, StationerySaleLine

MAX_LINES = 40


class StationerySaleService:
    @staticmethod
    @transaction.atomic
    def record(
        actor,
        lines: list[dict],
        *,
        payment_method: str = "cash",
        customer_name: str = "",
        note: str = "",
        request=None,
    ) -> StationerySale:
        """Sell the lines at the items' own sale prices and take them off the shelf. All or nothing."""

        if not lines:
            raise ValidationError({"lines": "Add at least one item."})
        if len(lines) > MAX_LINES:
            raise ValidationError({"lines": f"A sale cannot have more than {MAX_LINES} lines."})
        # Several lines for the same item are one movement; sort by id so concurrent sales lock rows in the same order.
        wanted: dict[int, Decimal] = defaultdict(lambda: Decimal("0"))
        for line in lines:
            wanted[line["item"]] += Decimal(line["quantity"])
        items = {
            item.pk: item
            for item in InventoryItem.objects.filter(pk__in=wanted, is_deleted=False, is_active=True, is_sellable=True)
        }
        if set(items) != set(wanted):
            raise ValidationError({"lines": "One of the items is not for sale."})
        currencies = {item.currency for item in items.values()}
        if len(currencies) != 1:
            raise ValidationError({"lines": "A sale cannot mix items priced in different currencies."})
        currency = currencies.pop()
        sale = StationerySale.objects.create(
            currency=currency,
            total=Decimal("0"),
            payment_method=payment_method,
            customer_name=customer_name.strip(),
            note=note.strip(),
            sold_by=actor,
        )
        total = Decimal("0")
        for item_id in sorted(wanted):
            item = items[item_id]
            quantity = wanted[item_id]
            line_total = money(item.sale_price * quantity)
            StationerySaleLine.objects.create(
                sale=sale,
                item=item,
                name=item.name,
                quantity=quantity,
                unit_price=item.sale_price,
                line_total=line_total,
            )
            InventoryService.record_movement(
                item, StockMovementType.USAGE, quantity, actor=actor, note=f"بيع قرطاسية #{sale.pk}"
            )
            total += line_total
        sale.total = money(total)
        sale.save(update_fields=["total", "updated_at"])
        AuditLogService.log(
            actor=actor,
            action=AuditAction.STATIONERY_SALE_RECORDED,
            target=sale,
            new_value={"total": str(sale.total), "currency": currency, "lines": len(wanted)},
            request=request,
        )
        return sale


def sales_report(period: Period) -> dict:
    sales = StationerySale.objects.filter(is_deleted=False, sold_at__gte=period.start, sold_at__lt=period.end)
    per_currency = {
        row["currency"]: {"total": money(row["total"]), "sales": row["sales"]}
        for row in sales.order_by().values("currency").annotate(total=Sum("total"), sales=Count("id"))
    }
    top = (
        StationerySaleLine.objects.filter(sale__in=sales)
        .values("item_id", "name", "sale__currency")
        .annotate(quantity=Sum("quantity"), revenue=Sum("line_total"))
        .order_by("-revenue")[:20]
    )
    return {
        "period": {"from": period.start.date(), "to": period.end.date()},
        "totals_by_currency": per_currency,
        "top_items": [
            {
                "item": row["item_id"],
                "name": row["name"],
                "currency": row["sale__currency"],
                "quantity": row["quantity"],
                "revenue": money(row["revenue"]),
            }
            for row in top
        ],
    }
