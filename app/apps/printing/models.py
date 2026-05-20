from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel


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


class PrintOrder(BaseModel):
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="print_orders")
    status = models.CharField(max_length=32, choices=PrintOrderStatus.choices, default=PrintOrderStatus.SUBMITTED)
    priority = models.CharField(max_length=32, choices=PrintOrderPriority.choices, default=PrintOrderPriority.NORMAL)
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
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

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "priority", "created_at"]),
            models.Index(fields=["user", "status"]),
            models.Index(fields=["assigned_to", "status"]),
        ]

    def __str__(self) -> str:
        return f"PrintOrder #{self.id}"


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


class PrintOrderItem(BaseModel):
    order = models.ForeignKey(PrintOrder, on_delete=models.CASCADE, related_name="items")
    source_file = models.ForeignKey("files.FileResource", on_delete=models.SET_NULL, related_name="print_order_items", null=True, blank=True)
    uploaded_file = models.FileField(upload_to="print_orders/", null=True, blank=True)
    original_file_name = models.CharField(max_length=255, blank=True)
    file_type = models.CharField(max_length=32, blank=True)
    file_size = models.PositiveBigIntegerField(default=0)
    pages_count = models.PositiveIntegerField(null=True, blank=True)
    copies = models.PositiveIntegerField(default=1)
    color_mode = models.CharField(max_length=32, choices=PrintColorMode.choices, default=PrintColorMode.BLACK_WHITE)
    paper_size = models.CharField(max_length=8, choices=PrintPaperSize.choices, default=PrintPaperSize.A4)
    sides = models.CharField(max_length=32, choices=PrintSides.choices, default=PrintSides.ONE_SIDED)
    binding = models.CharField(max_length=32, choices=PrintBinding.choices, default=PrintBinding.NONE)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    def clean(self):
        if not self.source_file and not self.uploaded_file:
            raise ValidationError("Either source_file or uploaded_file is required.")
        if self.copies < 1:
            raise ValidationError({"copies": "Copies must be at least 1."})

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
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["order", "created_at"])]
