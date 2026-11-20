"""Idempotent defaults for the stationery store: the categories and a few well-known brand NAMES.

No products, prices, stock or pictures are created: those are the owner's to enter (a brand with no product is not
shown to students). Safe to run on every deploy.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.store.models import StoreBrand, StoreCategory

CATEGORIES = [
    ("pens", "أقلام"),
    ("notebooks", "دفاتر"),
    ("office", "أدوات مكتبية"),
    ("drawing", "أدوات رسم وهندسة"),
    ("bags", "حقائب"),
    ("paper", "ورق وبطاقات"),
]
# Names only (international brands commonly stocked by stationery shops); the owner decides what is really sold.
BRANDS = ["Pilot", "Staedtler", "Faber-Castell", "Pelikan", "Stabilo", "Uni-ball", "Parker", "Rotring"]


class Command(BaseCommand):
    help = "Create the store categories and example brand names (idempotent; never overwrites staff edits)."

    @transaction.atomic
    def handle(self, *args, **options):
        made_categories = made_brands = 0
        for order, (slug, name) in enumerate(CATEGORIES, start=1):
            _, created = StoreCategory.objects.get_or_create(
                slug=slug, is_deleted=False, defaults={"name": name, "sort_order": order}
            )
            made_categories += int(created)
        for order, name in enumerate(BRANDS, start=1):
            _, created = StoreBrand.objects.get_or_create(name=name, is_deleted=False, defaults={"sort_order": order})
            made_brands += int(created)
        self.stdout.write(f"store defaults: {made_categories} categories and {made_brands} brands created")
