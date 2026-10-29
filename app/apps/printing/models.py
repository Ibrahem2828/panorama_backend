from __future__ import annotations

import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel

from .options import DuplexFlip, NUpDirection, PrintOrientation


class PrintOrderStatus(models.TextChoices):
    SUBMITTED = "submitted", "Submitted"
    UNDER_REVIEW = "under_review", "Under Review"
    ACCEPTED = "accepted", "Accepted"
    PRINTING = "printing", "Printing"
    READY = "ready", "Ready"
    DELIVERED = "delivered", "Delivered"
    CANCELLED = "cancelled", "Cancelled"
    REJECTED = "rejected", "Rejected"


class PrintOrderPriority(models.TextChoices):
    NORMAL = "normal", "Normal"
    STUDENT_PRIORITY = "student_priority", "Student Priority"
    URGENT = "urgent", "Urgent"


class PrintColorMode(models.TextChoices):
    BLACK_WHITE = "black_white", "Black and White"
    COLOR = "color", "Color"


class PrintPaperSize(models.TextChoices):
    A4 = "A4", "A4"
    A5 = "A5", "A5"
    A3 = "A3", "A3"


class PrintSides(models.TextChoices):
    ONE_SIDED = "one_sided", "One Sided"
    DOUBLE_SIDED = "double_sided", "Double Sided"


class PrintBinding(models.TextChoices):
    NONE = "none", "None"
    STAPLE = "staple", "Staple"
    SPIRAL = "spiral", "Spiral"
    THERMAL = "thermal", "Thermal"
    BEADS = "beads", "Beads"
    OTHER = "other", "Other"


class PrintPickupLocation(BaseModel):
    name = models.CharField(max_length=150)
    address = models.TextField(blank=True)
    instructions = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class PrintPricingRule(BaseModel):
    name = models.CharField(max_length=150)
    color_mode = models.CharField(max_length=32, choices=PrintColorMode.choices)
    paper_size = models.CharField(max_length=8, choices=PrintPaperSize.choices)
    sides = models.CharField(max_length=32, choices=PrintSides.choices)
    price_per_sheet = models.DecimalField(max_digits=12, decimal_places=2)
    setup_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency = models.CharField(max_length=8, default="SYP")
    is_active = models.BooleanField(default=True)
    effective_from = models.DateTimeField(default=timezone.now)
    effective_to = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-effective_from", "name"]
        indexes = [
            models.Index(fields=["color_mode", "paper_size", "sides", "is_active"], name="printing_rule_lookup_idx"),
            models.Index(fields=["effective_from", "effective_to"], name="printing_rule_effective_idx"),
        ]

    def clean(self):
        if self.price_per_sheet < 0 or self.setup_fee < 0:
            raise ValidationError("Pricing values cannot be negative.")
        if self.effective_to and self.effective_to <= self.effective_from:
            raise ValidationError({"effective_to": "The end date must be after the start date."})

    def __str__(self):
        return self.name


class BindingKind(models.TextChoices):
    """What a binding type uses up from the shelf (and how the wire is weighed): the stock mapping is by kind."""

    STAPLE = "staple", "Staple"
    SPIRAL = "spiral", "Spiral"
    THERMAL = "thermal", "Thermal"
    BEADS = "beads", "Beads"
    OTHER = "other", "Other"


class BindingPriceMode(models.TextChoices):
    PER_COPY = "per_copy", "A fixed price for every bound copy"
    PER_WIRE_KG = "per_wire_kg", "By the weight of the wire used"


class PrintBindingPrice(BaseModel):
    """A binding type the shop offers (the built-in ones plus any the owner adds) and its price in one currency."""

    binding = models.CharField(max_length=32, help_text="Short code, e.g. spiral or hard_cover.")
    name = models.CharField(
        max_length=100, blank=True, help_text="Shown to the customer; the code is shown when empty."
    )
    description = models.CharField(max_length=255, blank=True)
    kind = models.CharField(max_length=16, choices=BindingKind.choices, default=BindingKind.OTHER)
    price_mode = models.CharField(max_length=16, choices=BindingPriceMode.choices, default=BindingPriceMode.PER_COPY)
    price_per_copy = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    price_per_kg = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, help_text="Used when the price is by the weight of the wire."
    )
    sort_order = models.PositiveSmallIntegerField(default=0)
    currency = models.CharField(max_length=8, default="SYP")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "binding"]
        constraints = [
            models.UniqueConstraint(fields=["binding", "currency"], name="printing_binding_currency_uniq"),
        ]

    def clean(self):
        if self.price_per_copy < 0 or self.price_per_kg < 0:
            raise ValidationError("Binding price cannot be negative.")
        if self.binding in {"", PrintBinding.NONE}:
            raise ValidationError({"binding": "This code is reserved."})

    def save(self, *args, **kwargs):
        # The built-in codes (staple, spiral, thermal, beads) are their own kind unless the owner chose another.
        if self.kind == BindingKind.OTHER and self.binding in set(BindingKind.values) - {BindingKind.OTHER}:
            self.kind = self.binding
        super().save(*args, **kwargs)

    @property
    def display_name(self) -> str:
        return self.name or self.binding

    def __str__(self):
        return self.binding


class SpiralWireSpec(BaseModel):
    """How much wire (in grams) one bound copy takes, by the number of sheets in the copy. Filled in by the shop owner."""

    name = models.CharField(max_length=100, blank=True)
    min_sheets = models.PositiveIntegerField()
    max_sheets = models.PositiveIntegerField()
    weight_grams = models.DecimalField(max_digits=8, decimal_places=2)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["min_sheets"]

    def clean(self):
        if self.max_sheets < self.min_sheets:
            raise ValidationError({"max_sheets": "The upper limit must not be below the lower limit."})
        if self.weight_grams <= 0:
            raise ValidationError({"weight_grams": "The weight must be positive."})

    def __str__(self):
        return f"{self.min_sheets}-{self.max_sheets} sheets: {self.weight_grams} g"


class PrintCoverOption(BaseModel):
    """A cover the customer can add to a bound copy (name, colour shown in the simulation and the price)."""

    code = models.CharField(max_length=32)
    name = models.CharField(max_length=100)
    color_hex = models.CharField(max_length=7, default="#1E3A8A", help_text="Cover colour, e.g. #1E3A8A.")
    price_per_copy = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency = models.CharField(max_length=8, default="SYP")
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        constraints = [models.UniqueConstraint(fields=["code", "currency"], name="printing_cover_currency_uniq")]

    def clean(self):
        if self.price_per_copy < 0:
            raise ValidationError("Cover price cannot be negative.")
        if len(self.color_hex) != 7 or not self.color_hex.startswith("#"):
            raise ValidationError({"color_hex": "Use a colour like #1E3A8A."})

    def __str__(self):
        return self.name


class PrintOrderSource(models.TextChoices):
    APP = "app", "Mobile or web app"
    COUNTER = "counter", "Print shop counter"


class PrintOrder(BaseModel):
    # A counter (walk-in) order has no account: the customer is only a name and a phone number.
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="print_orders", null=True, blank=True
    )
    source = models.CharField(
        max_length=16, choices=PrintOrderSource.choices, default=PrintOrderSource.APP, db_index=True
    )
    customer_name = models.CharField(max_length=150, blank=True)
    customer_phone = models.CharField(max_length=32, blank=True)
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, related_name="counter_print_orders", null=True, blank=True
    )
    idempotency_key_hash = models.CharField(max_length=64, blank=True, editable=False)
    status = models.CharField(max_length=32, choices=PrintOrderStatus.choices, default=PrintOrderStatus.SUBMITTED)
    priority = models.CharField(max_length=32, choices=PrintOrderPriority.choices, default=PrintOrderPriority.NORMAL)
    total_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency = models.CharField(max_length=8, default="SYP")
    pricing_snapshot = models.JSONField(default=dict, blank=True)
    pricing_revision = models.CharField(max_length=64, blank=True, db_index=True)
    price_calculated_at = models.DateTimeField(null=True, blank=True)
    pickup_location = models.ForeignKey(
        PrintPickupLocation,
        on_delete=models.PROTECT,
        related_name="orders",
        null=True,
        blank=True,
    )
    user_notes = models.TextField(blank=True)
    internal_notes = models.TextField(blank=True)
    assigned_to = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        related_name="assigned_print_orders",
        null=True,
        blank=True,
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    rejected_reason = models.TextField(blank=True)
    # Set once the stock this order used has been deducted, so delivering twice never deducts twice.
    stock_deducted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "priority", "created_at"]),
            models.Index(fields=["user", "status"]),
            models.Index(fields=["assigned_to", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "idempotency_key_hash"],
                condition=~models.Q(idempotency_key_hash=""),
                name="unique_print_order_idempotency_key",
            )
        ]

    @property
    def customer_display(self) -> str:
        """Who the order is for: the account holder, or the name typed in at the counter."""

        if self.user is not None:
            return self.user.full_name
        return self.customer_name or "زبون المحل"

    def __str__(self) -> str:
        return f"PrintOrder #{self.id}"


class PrintOrderItem(BaseModel):
    order = models.ForeignKey(PrintOrder, on_delete=models.CASCADE, related_name="items")
    source_file = models.ForeignKey(
        "files.FileResource", on_delete=models.SET_NULL, related_name="print_order_items", null=True, blank=True
    )
    uploaded_file = models.FileField(upload_to="print_orders/", null=True, blank=True)
    original_file_name = models.CharField(max_length=255, blank=True)
    file_type = models.CharField(max_length=32, blank=True)
    file_size = models.PositiveBigIntegerField(default=0)
    pages_count = models.PositiveIntegerField(null=True, blank=True)
    copies = models.PositiveIntegerField(default=1)
    color_mode = models.CharField(max_length=32, choices=PrintColorMode.choices, default=PrintColorMode.BLACK_WHITE)
    paper_size = models.CharField(max_length=8, choices=PrintPaperSize.choices, default=PrintPaperSize.A4)
    sides = models.CharField(max_length=32, choices=PrintSides.choices, default=PrintSides.ONE_SIDED)
    binding = models.CharField(max_length=32, default=PrintBinding.NONE)
    binding_note = models.CharField(max_length=120, blank=True)
    # Print-dialog options (all defaults reproduce the old behaviour: one page per sheet, every page, collated).
    pages_per_sheet = models.PositiveSmallIntegerField(default=1)
    n_up_direction = models.CharField(max_length=8, choices=NUpDirection.choices, default=NUpDirection.LTR)
    duplex_flip = models.CharField(max_length=16, choices=DuplexFlip.choices, default=DuplexFlip.LONG_EDGE)
    orientation = models.CharField(max_length=16, choices=PrintOrientation.choices, default=PrintOrientation.AUTO)
    page_range = models.CharField(max_length=255, blank=True)
    selected_pages = models.PositiveIntegerField(null=True, blank=True, help_text="Pages printed per copy.")
    collate = models.BooleanField(default=True)
    cover = models.CharField(max_length=32, blank=True)
    cover_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    product = models.ForeignKey(
        "printing.ReadyProduct", on_delete=models.SET_NULL, related_name="order_items", null=True, blank=True
    )
    sheets_count = models.PositiveIntegerField(default=0)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    binding_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    pricing_snapshot = models.JSONField(default=dict, blank=True)

    def clean(self):
        # A counter order is typed in at the shop: there is no file to attach.
        counter = self.order_id and self.order.source == PrintOrderSource.COUNTER
        if not counter and not self.product_id and bool(self.source_file) == bool(self.uploaded_file):
            raise ValidationError("Exactly one of source_file or uploaded_file is required.")
        if not 1 <= self.copies <= 99:
            raise ValidationError({"copies": "Copies must be between 1 and 99."})
        if not self.pages_count or self.pages_count < 1:
            raise ValidationError({"pages_count": "The document must contain at least one page."})

    def save(self, *args, **kwargs):
        source = self.uploaded_file or getattr(self.source_file, "file", None)
        if source:
            self.original_file_name = self.original_file_name or Path(source.name).name
            self.file_type = Path(source.name).suffix.lower().lstrip(".")[:32]
            self.file_size = getattr(source, "size", self.file_size) or self.file_size or 0
        super().save(*args, **kwargs)


class PrintOrderStatusHistory(BaseModel):
    order = models.ForeignKey(PrintOrder, on_delete=models.CASCADE, related_name="status_history")
    old_status = models.CharField(max_length=32, choices=PrintOrderStatus.choices, blank=True)
    new_status = models.CharField(max_length=32, choices=PrintOrderStatus.choices)
    changed_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="print_status_changes")
    public_note = models.TextField(blank=True)
    internal_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["order", "created_at"])]


class PrintItemAccessTicket(BaseModel):
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    item = models.ForeignKey(PrintOrderItem, on_delete=models.CASCADE, related_name="access_tickets")
    requested_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="print_item_access_tickets",
    )
    expires_at = models.DateTimeField()
    max_uses = models.PositiveSmallIntegerField(default=8)
    use_count = models.PositiveSmallIntegerField(default=0)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["token", "expires_at"], name="printing_item_ticket_idx")]

    @property
    def is_valid(self) -> bool:
        return (
            self.revoked_at is None
            and self.expires_at > timezone.now()
            and self.use_count < self.max_uses
            and not self.item.is_deleted
            and not self.item.order.is_deleted
        )

    @classmethod
    def issue(cls, item, requested_by):
        from datetime import timedelta

        return cls.objects.create(
            item=item,
            requested_by=requested_by,
            expires_at=timezone.now() + timedelta(seconds=settings.FILE_ACCESS_TICKET_TTL_SECONDS),
            max_uses=settings.FILE_ACCESS_TICKET_MAX_USES,
        )


class PrintConversionStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    READY = "ready", "Ready"
    FAILED = "failed", "Failed"


class PrintConversion(BaseModel):
    """A Word/PowerPoint upload converted to PDF on the conversion worker so it can be priced and printed.

    The web runtime has no office suite (deliberately); it stores the upload, queues the task and clients reference
    the finished PDF by ``token`` as ``converted_upload`` on a print item. Rows and files are purged after ``expires_at``.
    """

    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="print_conversions")
    original_name = models.CharField(max_length=255)
    status = models.CharField(
        max_length=16, choices=PrintConversionStatus.choices, default=PrintConversionStatus.PENDING
    )
    input_file = models.FileField(upload_to="print_conversions/in/", null=True, blank=True)
    pdf_file = models.FileField(upload_to="print_conversions/out/", null=True, blank=True)
    pages_count = models.PositiveIntegerField(null=True, blank=True)
    failure_message = models.CharField(max_length=255, blank=True)
    expires_at = models.DateTimeField()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "created_at"], name="printing_conv_user_idx")]

    @property
    def pdf_name(self) -> str:
        return f"{Path(self.original_name).stem or 'document'}.pdf"


class ReadyProduct(BaseModel):
    """A ready-made printed product with a fixed specification and a fixed price (e.g. a dental anatomy notebook)."""

    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to="print_products/", null=True, blank=True)
    source_file = models.ForeignKey(
        "files.FileResource", on_delete=models.SET_NULL, related_name="ready_products", null=True, blank=True
    )
    pages_count = models.PositiveIntegerField(default=1)
    paper_size = models.CharField(max_length=8, choices=PrintPaperSize.choices, default=PrintPaperSize.A4)
    color_mode = models.CharField(max_length=32, choices=PrintColorMode.choices, default=PrintColorMode.BLACK_WHITE)
    sides = models.CharField(max_length=32, choices=PrintSides.choices, default=PrintSides.DOUBLE_SIDED)
    binding = models.CharField(max_length=32, default=PrintBinding.NONE)
    cover = models.CharField(max_length=32, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=8, default="SYP")
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def clean(self):
        if self.price < 0:
            raise ValidationError({"price": "The price cannot be negative."})

    def __str__(self):
        return self.name


class PrintJobStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    PRINTING = "printing", "Printing"
    DONE = "done", "Done"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class Printer(BaseModel):
    """A physical printer the silent-print agent drives; the agent's heartbeat keeps ``last_seen_at`` fresh."""

    name = models.CharField(max_length=100)
    station = models.CharField(max_length=100, blank=True, help_text="Where it stands, e.g. the shop or location.")
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    notes = models.CharField(max_length=500, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=16, blank=True)
    last_detail = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["name"], condition=models.Q(is_deleted=False), name="printing_printer_name_unique"
            ),
            models.UniqueConstraint(
                fields=["is_default"],
                condition=models.Q(is_default=True, is_deleted=False),
                name="printing_printer_single_default",
            ),
        ]

    def __str__(self):
        return self.name


class PrintJob(BaseModel):
    """One file of an order sent to the printer from the dashboard (or the silent-print agent)."""

    order = models.ForeignKey(PrintOrder, on_delete=models.CASCADE, related_name="print_jobs")
    item = models.ForeignKey(PrintOrderItem, on_delete=models.CASCADE, related_name="print_jobs")
    status = models.CharField(max_length=16, choices=PrintJobStatus.choices, default=PrintJobStatus.QUEUED)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="print_jobs"
    )
    printer_name = models.CharField(max_length=150, blank=True)
    printer = models.ForeignKey(Printer, null=True, blank=True, on_delete=models.SET_NULL, related_name="jobs")
    sides_printed = models.PositiveIntegerField(default=0, help_text="Faces in the print-ready file.")
    sheets = models.PositiveIntegerField(default=0)
    options = models.JSONField(default=dict, blank=True)
    error = models.CharField(max_length=255, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["status", "created_at"], name="printing_job_status_idx")]
