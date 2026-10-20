"""Creates INITIAL placeholder prices, an exchange rate, a pickup point and sample stock so the shop can start working.

Everything is created only when it is missing (get_or_create) and never modified afterwards, so re-running it after the owner
edited a price or a quantity in the dashboard changes nothing. All values are placeholders for the owner to replace.
"""

from __future__ import annotations

from decimal import Decimal

from apps.finance.models import ExchangeRate, FinanceCurrency, InventoryCategory, InventoryItem
from apps.printing.models import (
    PrintBinding,
    PrintBindingPrice,
    PrintColorMode,
    PrintPaperSize,
    PrintPickupLocation,
    PrintPricingRule,
    PrintSides,
)
from django.core.management.base import BaseCommand
from django.db import transaction

SYP_PER_USD = Decimal("120")  # placeholder: 1 US dollar = 120 NEW Syrian pounds

COLORS = {PrintColorMode.BLACK_WHITE: "أبيض وأسود", PrintColorMode.COLOR: "ملوّن"}
SIDES = {PrintSides.ONE_SIDED: "وجه واحد", PrintSides.DOUBLE_SIDED: "وجهان"}

# (paper size, colour, sides) -> (USD per sheet, new SYP per sheet)
SHEET_PRICES = {
    (PrintPaperSize.A5, PrintColorMode.BLACK_WHITE, PrintSides.ONE_SIDED): ("0.02", "2.50"),
    (PrintPaperSize.A5, PrintColorMode.BLACK_WHITE, PrintSides.DOUBLE_SIDED): ("0.03", "4"),
    (PrintPaperSize.A5, PrintColorMode.COLOR, PrintSides.ONE_SIDED): ("0.08", "10"),
    (PrintPaperSize.A5, PrintColorMode.COLOR, PrintSides.DOUBLE_SIDED): ("0.14", "17"),
    (PrintPaperSize.A4, PrintColorMode.BLACK_WHITE, PrintSides.ONE_SIDED): ("0.03", "4"),
    (PrintPaperSize.A4, PrintColorMode.BLACK_WHITE, PrintSides.DOUBLE_SIDED): ("0.05", "6"),
    (PrintPaperSize.A4, PrintColorMode.COLOR, PrintSides.ONE_SIDED): ("0.15", "18"),
    (PrintPaperSize.A4, PrintColorMode.COLOR, PrintSides.DOUBLE_SIDED): ("0.25", "30"),
    (PrintPaperSize.A3, PrintColorMode.BLACK_WHITE, PrintSides.ONE_SIDED): ("0.08", "10"),
    (PrintPaperSize.A3, PrintColorMode.BLACK_WHITE, PrintSides.DOUBLE_SIDED): ("0.14", "17"),
    (PrintPaperSize.A3, PrintColorMode.COLOR, PrintSides.ONE_SIDED): ("0.40", "48"),
    (PrintPaperSize.A3, PrintColorMode.COLOR, PrintSides.DOUBLE_SIDED): ("0.70", "84"),
}
# binding -> (USD per copy, new SYP per copy)
BINDING_PRICES = {
    PrintBinding.NONE: ("0", "0"),
    PrintBinding.STAPLE: ("0.05", "6"),
    PrintBinding.SPIRAL: ("0.60", "72"),
    PrintBinding.THERMAL: ("1.00", "120"),
}

# What a delivered order uses of the seeded items: (consumption key, stock units per sheet / bound copy).
# A 500-sheet ream -> 0.002 per sheet; a box of 100 covers -> 0.01 per spiral-bound copy; a box of ~5000 staples, 2 per copy.
CONSUMPTION = {
    "ورق A4 - 80 غ": ("paper_a4", "0.002"),
    "ورق A3 - 80 غ": ("paper_a3", "0.002"),
    "ورق A5 - 80 غ": ("paper_a5", "0.002"),
    "أغلفة تجليد حلزوني": ("binding_spiral", "0.01"),
    "دبابيس تدبيس": ("binding_staple", "0.0004"),
}

# (category, name, unit, quantity, reorder level, unit cost in USD)
STOCK = [
    (InventoryCategory.PAPER, "ورق A4 - 80 غ", "رزمة (500 ورقة)", "20", "5", "4.00"),
    (InventoryCategory.PAPER, "ورق A3 - 80 غ", "رزمة (500 ورقة)", "5", "2", "9.00"),
    (InventoryCategory.PAPER, "ورق A5 - 80 غ", "رزمة (500 ورقة)", "5", "2", "2.50"),
    (InventoryCategory.INK, "حبر أسود", "زجاجة (1 لتر)", "4", "2", "18.00"),
    (InventoryCategory.INK, "حبر سماوي (Cyan)", "زجاجة (1 لتر)", "2", "1", "18.00"),
    (InventoryCategory.INK, "حبر أرجواني (Magenta)", "زجاجة (1 لتر)", "2", "1", "18.00"),
    (InventoryCategory.INK, "حبر أصفر (Yellow)", "زجاجة (1 لتر)", "2", "1", "18.00"),
    (InventoryCategory.PENS, "أقلام حبر جاف أزرق", "علبة (50 قلم)", "5", "2", "6.00"),
    (InventoryCategory.PENS, "أقلام تحديد", "علبة (10 أقلام)", "5", "2", "4.00"),
    (InventoryCategory.PRINTER, "طابعة ليزر أبيض وأسود", "قطعة", "1", "0", "350.00"),
    (InventoryCategory.PRINTER, "طابعة ملونة بخزان حبر", "قطعة", "1", "0", "500.00"),
    (InventoryCategory.OTHER, "دبابيس تدبيس", "علبة", "10", "3", "0.80"),
    (InventoryCategory.OTHER, "أغلفة تجليد حلزوني", "علبة (100 غلاف)", "3", "1", "7.00"),
]


class Command(BaseCommand):
    help = (
        "Create initial placeholder prices (new SYP and USD), exchange rate, pickup point and sample stock. Idempotent."
    )

    @transaction.atomic
    def handle(self, *args, **options):
        counts = {"rules": 0, "bindings": 0, "rate": 0, "pickup": 0, "stock": 0}
        syp_per_usd = SYP_PER_USD

        for (size, color, sides), (usd, syp) in SHEET_PRICES.items():
            for currency, price in ((FinanceCurrency.USD, usd), (FinanceCurrency.SYP, syp)):
                name = f"{size} - {COLORS[color]} - {SIDES[sides]} - {currency} (مبدئي)"
                _, created = PrintPricingRule.objects.get_or_create(
                    name=name,
                    defaults={
                        "color_mode": color,
                        "paper_size": size,
                        "sides": sides,
                        "price_per_sheet": Decimal(price),
                        "setup_fee": Decimal("0"),
                        "currency": currency,
                        "is_active": True,
                    },
                )
                counts["rules"] += int(created)

        for binding, (usd, syp) in BINDING_PRICES.items():
            for currency, price in ((FinanceCurrency.USD, usd), (FinanceCurrency.SYP, syp)):
                _, created = PrintBindingPrice.objects.get_or_create(
                    binding=binding,
                    currency=currency,
                    defaults={"price_per_copy": Decimal(price), "is_active": True},
                )
                counts["bindings"] += int(created)

        if not ExchangeRate.objects.filter(is_deleted=False).exists():
            ExchangeRate.objects.create(syp_per_usd=syp_per_usd, note="قيمة مبدئية - عدّلها حسب سعر الصرف الفعلي")
            counts["rate"] = 1

        _, created = PrintPickupLocation.objects.get_or_create(
            name="مركز الطباعة - الجامعة السورية الخاصة (مبدئي)",
            defaults={
                "address": "داخل حرم الجامعة السورية الخاصة، ريف دمشق - عدّل العنوان الفعلي",
                "instructions": "يرجى إبراز رقم الطلب عند الاستلام.",
                "is_active": True,
            },
        )
        counts["pickup"] = int(created)

        for category, name, unit, quantity, reorder, usd_cost in STOCK:
            item, created = InventoryItem.objects.get_or_create(
                category=category,
                name=name,
                is_deleted=False,
                defaults={
                    "unit": unit,
                    "quantity": Decimal(quantity),
                    "reorder_level": Decimal(reorder),
                    "unit_cost": Decimal(usd_cost),
                    "currency": FinanceCurrency.USD,
                    "notes": "بيانات مبدئية - عدّل الكمية والتكلفة",
                },
            )
            counts["stock"] += int(created)
            mapping = CONSUMPTION.get(name)
            # Only fill an item that has no rule yet: a rule the owner edited is never overwritten.
            if mapping and not item.consumption_key and item.consumption_per_use == 0:
                if not InventoryItem.objects.filter(consumption_key=mapping[0], is_deleted=False).exists():
                    item.consumption_key, item.consumption_per_use = mapping[0], Decimal(mapping[1])
                    item.save(update_fields=["consumption_key", "consumption_per_use", "updated_at"])

        self.stdout.write(self.style.SUCCESS(f"Seeded (created only what was missing): {counts}"))
