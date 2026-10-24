from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel


class FinanceCurrency(models.TextChoices):
    """The two currencies the print shop trades in: the NEW Syrian pound (after the redenomination) and the US dollar."""

    SYP = "SYP", "Syrian Pound (new)"
    USD = "USD", "US Dollar"


def money_field(**kwargs):
    return models.DecimalField(max_digits=14, decimal_places=2, **kwargs)


def quantity_field(**kwargs):
    return models.DecimalField(max_digits=14, decimal_places=2, **kwargs)


class ExchangeRate(BaseModel):
    """How many NEW Syrian pounds one US dollar is worth from ``effective_from`` on (latest row wins)."""

    effective_from = models.DateTimeField(default=timezone.now)
    syp_per_usd = models.DecimalField(
        max_digits=14, decimal_places=4, validators=[MinValueValidator(Decimal("0.0001"))]
    )
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-effective_from", "-id"]
        indexes = [models.Index(fields=["effective_from"], name="finance_rate_effective_idx")]

    @classmethod
    def current(cls, at=None) -> ExchangeRate | None:
        return cls.objects.filter(is_deleted=False, effective_from__lte=at or timezone.now()).first()

    def __str__(self) -> str:
        return f"1 USD = {self.syp_per_usd} SYP"


class InventoryCategory(models.TextChoices):
    PAPER = "paper", "Paper"
    INK = "ink", "Ink and toner"
    PENS = "pens", "Pens and stationery"
    PRINTER = "printer", "Printers and machines"
    OTHER = "other", "Other supplies"


class ConsumptionKey(models.TextChoices):
    """What a delivered print order uses up. One stock item may carry each key (paper per size, binding per kind)."""

    PAPER_A5 = "paper_a5", "Paper A5 (one sheet)"
    PAPER_A4 = "paper_a4", "Paper A4 (one sheet)"
    PAPER_A3 = "paper_a3", "Paper A3 (one sheet)"
    BINDING_STAPLE = "binding_staple", "Staple binding (one bound copy)"
    BINDING_SPIRAL = "binding_spiral", "Spiral binding (one bound copy)"
    BINDING_THERMAL = "binding_thermal", "Thermal binding (one bound copy)"
    BINDING_BEADS = "binding_beads", "Beads binding (one bound copy)"
    # Ink is estimated per printed page: black is used on every page, the three colours only on colour pages.
    INK_BLACK = "ink_black", "Black ink (one printed page)"
    INK_CYAN = "ink_cyan", "Cyan ink (one colour page)"
    INK_MAGENTA = "ink_magenta", "Magenta ink (one colour page)"
    INK_YELLOW = "ink_yellow", "Yellow ink (one colour page)"
    PER_SHEET = "per_sheet", "Every sheet, any size (drum, maintenance)"
    PER_ORDER = "per_order", "Every delivered order (bag, receipt roll)"


class InventoryItem(BaseModel):
    """A stocked thing: paper, ink, pens, printers... ``quantity`` only ever changes through ``StockMovement`` rows."""

    name = models.CharField(max_length=150)
    category = models.CharField(max_length=16, choices=InventoryCategory.choices, default=InventoryCategory.OTHER)
    unit = models.CharField(max_length=32, default="piece")
    sku = models.CharField(max_length=64, blank=True)
    quantity = quantity_field(default=Decimal("0"))
    reorder_level = quantity_field(default=Decimal("0"), validators=[MinValueValidator(Decimal("0"))])
    unit_cost = money_field(default=Decimal("0"), validators=[MinValueValidator(Decimal("0"))])
    currency = models.CharField(max_length=3, choices=FinanceCurrency.choices, default=FinanceCurrency.SYP)
    location = models.CharField(max_length=150, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    # Automatic usage: a delivered order takes ``consumption_per_use`` stock units for every sheet / bound copy it used.
    consumption_key = models.CharField(max_length=24, choices=ConsumptionKey.choices, blank=True)
    consumption_per_use = models.DecimalField(
        max_digits=14,
        decimal_places=6,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Stock units used per sheet or bound copy, e.g. 0.002 for a 500-sheet ream.",
    )
    # Used stock below one hundredth of a unit is kept here until it adds up to a whole hundredth (quantity has 2 places).
    pending_consumption = models.DecimalField(max_digits=14, decimal_places=6, default=Decimal("0"), editable=False)

    class Meta:
        ordering = ["category", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["category", "name"], condition=models.Q(is_deleted=False), name="finance_item_unique_live_name"
            ),
            models.UniqueConstraint(
                fields=["consumption_key"],
                condition=models.Q(is_deleted=False) & ~models.Q(consumption_key=""),
                name="finance_item_unique_consumption_key",
            ),
        ]
        indexes = [models.Index(fields=["category", "is_active"], name="finance_item_category_idx")]

    @property
    def is_low(self) -> bool:
        return self.is_active and self.reorder_level > 0 and self.quantity <= self.reorder_level

    @property
    def stock_value(self) -> Decimal:
        return (self.quantity * self.unit_cost).quantize(Decimal("0.01"))

    def __str__(self) -> str:
        return self.name


class WasteReason(models.TextChoices):
    """Why stock was thrown away; shown in the stock report so waste can be told apart from normal use."""

    JAM = "jam", "Paper jam or misfeed"
    MISPRINT = "misprint", "Misprint or wrong settings"
    DAMAGED = "damaged", "Damaged or wet"
    EXPIRED = "expired", "Expired or dried out"
    LOST = "lost", "Lost"
    OTHER = "other", "Other"


class StockMovementType(models.TextChoices):
    PURCHASE = "purchase", "Purchase (adds stock)"
    USAGE = "usage", "Usage (consumes stock)"
    WASTE = "waste", "Waste or damage (removes stock)"
    ADJUSTMENT = "adjustment", "Count adjustment (signed)"


class StockMovement(BaseModel):
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name="movements")
    movement_type = models.CharField(max_length=16, choices=StockMovementType.choices)
    quantity = quantity_field(help_text="Signed change applied to the item's quantity.")
    quantity_after = quantity_field()
    unit_cost = money_field(null=True, blank=True)
    currency = models.CharField(max_length=3, choices=FinanceCurrency.choices, blank=True)
    note = models.CharField(max_length=255, blank=True)
    waste_reason = models.CharField(max_length=16, choices=WasteReason.choices, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    expense = models.ForeignKey(
        "finance.Expense", null=True, blank=True, on_delete=models.SET_NULL, related_name="movements"
    )
    # Set when the movement is the automatic usage of a delivered print order.
    order = models.ForeignKey(
        "printing.PrintOrder", null=True, blank=True, on_delete=models.SET_NULL, related_name="stock_movements"
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["item", "created_at"], name="finance_move_item_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=["order", "item"],
                condition=models.Q(order__isnull=False) & models.Q(is_deleted=False),
                name="finance_move_unique_order_item",
            )
        ]


class ExpenseCategory(models.TextChoices):
    SUPPLIES = "supplies", "Supplies (paper, ink, pens)"
    EQUIPMENT = "equipment", "Equipment and printers"
    MAINTENANCE = "maintenance", "Maintenance and repairs"
    SALARIES = "salaries", "Salaries and wages"
    RENT = "rent", "Rent"
    UTILITIES = "utilities", "Electricity, internet and utilities"
    TRANSPORT = "transport", "Transport"
    OTHER = "other", "Other"


class PurchaseKind(models.TextChoices):
    """The four buckets of the purchases screen (what the owner bought, not how it is accounted)."""

    PURCHASES = "purchases", "Purchases"
    CONSUMABLES = "consumables", "Consumables"
    MISCELLANEOUS = "miscellaneous", "Miscellaneous"
    EQUIPMENT = "equipment", "Equipment"


class PurchaseReminder(BaseModel):
    """ "Buy this again every N weeks": ink, cleaning supplies... The shop records what it BOUGHT; consumption is never estimated."""

    name = models.CharField(max_length=120)
    interval_weeks = models.PositiveSmallIntegerField(default=10, validators=[MinValueValidator(1)])
    is_active = models.BooleanField(default=True)
    #: Last time staff were told it is due, so the daily check says it once per due period, not every day.
    last_notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["name"], condition=models.Q(is_deleted=False), name="finance_reminder_unique_name"
            )
        ]

    def last_purchase_date(self):
        row = self.expenses.filter(is_deleted=False).order_by("-spent_on", "-id").first()
        return row.spent_on if row else None

    def __str__(self) -> str:
        return self.name


class Expense(BaseModel):
    spent_on = models.DateField(default=timezone.localdate)
    category = models.CharField(max_length=16, choices=ExpenseCategory.choices, default=ExpenseCategory.OTHER)
    #: The purchases-screen bucket. Old rows (and stock purchases booked automatically) keep ``miscellaneous``/``purchases``.
    kind = models.CharField(max_length=16, choices=PurchaseKind.choices, default=PurchaseKind.MISCELLANEOUS)
    description = models.CharField(max_length=255)
    amount = money_field(validators=[MinValueValidator(Decimal("0.01"))])
    currency = models.CharField(max_length=3, choices=FinanceCurrency.choices, default=FinanceCurrency.SYP)
    supplier = models.CharField(max_length=150, blank=True)
    notes = models.TextField(blank=True)
    invoice = models.FileField(upload_to="finance/invoices/", blank=True)
    reminder = models.ForeignKey(
        PurchaseReminder, null=True, blank=True, on_delete=models.SET_NULL, related_name="expenses"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-spent_on", "-id"]
        indexes = [models.Index(fields=["spent_on", "category"], name="finance_expense_date_idx")]


class PaymentMethod(models.TextChoices):
    CASH = "cash", "Cash"
    BANK_TRANSFER = "bank_transfer", "Bank transfer"
    MOBILE_WALLET = "mobile_wallet", "Mobile wallet"
    OTHER = "other", "Other"


class OrderPayment(BaseModel):
    """Money actually collected for a print order (an order is 'paid' when its payments reach its total)."""

    order = models.ForeignKey("printing.PrintOrder", on_delete=models.PROTECT, related_name="payments")
    amount = money_field(validators=[MinValueValidator(Decimal("0.01"))])
    currency = models.CharField(max_length=3, choices=FinanceCurrency.choices)
    method = models.CharField(max_length=16, choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    received_at = models.DateTimeField(default=timezone.now)
    note = models.CharField(max_length=255, blank=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-received_at", "-id"]
        indexes = [models.Index(fields=["order"], name="finance_payment_order_idx")]

    def clean(self):
        if self.order_id and self.currency and self.currency != self.order.currency:
            raise ValidationError({"currency": "A payment must be in the currency of the order."})
