"""Over-the-counter sale of stationery (pens, notebooks...): items come from the stock, the sale takes them off the shelf."""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel
from apps.finance.models import FinanceCurrency, PaymentMethod


class StationerySale(BaseModel):
    sold_at = models.DateTimeField(default=timezone.now, db_index=True)
    currency = models.CharField(max_length=3, choices=FinanceCurrency.choices, default=FinanceCurrency.SYP)
    total = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0"))])
    payment_method = models.CharField(max_length=16, choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    customer_name = models.CharField(max_length=150, blank=True)
    note = models.CharField(max_length=255, blank=True)
    sold_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="stationery_sales"
    )

    class Meta:
        ordering = ["-sold_at", "-id"]


class StationerySaleLine(BaseModel):
    sale = models.ForeignKey(StationerySale, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("finance.InventoryItem", on_delete=models.PROTECT, related_name="sale_lines")
    name = models.CharField(max_length=150, help_text="The item's name when it was sold.")
    quantity = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    line_total = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ["id"]
