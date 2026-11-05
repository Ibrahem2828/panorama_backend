"""Loads the print shop's paper/material option catalog ("خيارات الورق حسب القسم") in new Syrian pounds and USD.

Rows are created only when missing (get_or_create on section + code + currency) and never modified afterwards, so re-running
it after the owner edited a price, a name or switched an option off changes nothing. USD = SYP / 120 (the same placeholder
rate as seed_print_finance_defaults), rounded to 2 decimals. New-SYP amounts are exactly the ones given by the owner.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.finance.models import FinanceCurrency
from apps.printing.models import PrintExtraOption, PrintExtraPriceUnit, PrintExtraSection

SYP_PER_USD = Decimal("120")  # placeholder, as in seed_print_finance_defaults

S = PrintExtraSection
PER_COPY, PER_50, PER_SHEET = (
    PrintExtraPriceUnit.PER_COPY,
    PrintExtraPriceUnit.PER_50_SHEETS,
    PrintExtraPriceUnit.PER_SHEET,
)

# (code, name, new SYP price, price unit, is_active, description)
_Row = tuple[str, str, str, str, bool, str]

NEAM_PINHOLE = ("neam_pinhole_300", "NEAM CARD GLOSSY WATER PROOF PINHOLE GRAIN 300 غ", "75")
NEAM_BARK = ("neam_bark_300", "NEAM CARD GLOSSY WATER PROOF BARK GRAIN 300 غ", "75")
CERTIFICATE = ("certificate_cream_220", "كرتون شهادة كريمي مقشش 220 غ", "25")


def _rows(*items: tuple[str, str, str], unit: str = PER_COPY) -> list[_Row]:
    return [(code, name, price, unit, True, "") for code, name, price in items]


COVER: list[_Row] = _rows(
    ("none", "لاشيء", "0"),
    ("paper", "ورق", "5"),
    ("colored_paper_80", "ورق ملون 80 غ", "5"),
    ("card_130", "كرتون 130 غ", "10"),
    ("card_230", "كرتون 230 غ", "15"),
)
TRANSPARENCY_REQUIRED: list[_Row] = _rows(
    ("transparency_150", "شفافية 150 ميكرون", "10"),
    ("transparency_300", "شفافية 300 ميكرون", "15"),
)
TRANSPARENCY_OPTIONAL: list[_Row] = _rows(
    ("card_130", "كرتون 130 غ", "10"),
    ("card_230", "كرتون 230 غ", "15"),
    ("white_plastic_200", "بلاستيك أبيض 200 غ", "15"),
    ("card_230_green", "كرتون ملون 230 غ أخضر", "15"),
    ("card_230_pink", "كرتون ملون 230 غ زهري", "15"),
    ("card_230_blue", "كرتون ملون 230 غ أزرق", "15"),
    ("black_plastic_700", "بلاستيك أسود 700 غ", "20"),
    ("colored_paper_80", "ورق ملون 80 غ", "5"),
    NEAM_PINHOLE,
    NEAM_BARK,
    CERTIFICATE,
)
FINAL_DELIVERY: list[_Row] = [
    ("bag", "كيس", "0", PER_COPY, True, "السعر غير محدد في النموذج الأصلي: 0 مؤقتاً، يحدده المالك."),
    ("punched_folder_30", "مصنف مثقب 30 ميكرون", "5", PER_COPY, True, ""),
    ("punched_folder_60", "مصنف مثقب 60 ميكرون", "10", PER_COPY, True, ""),
    ("punched_folder_80", "مصنف مثقب 80 ميكرون", "15", PER_COPY, True, ""),
    ("none", "لا شيء", "0", PER_COPY, True, ""),
]
FINISHING: list[_Row] = [
    ("wire_comb", "تسليك", "20", PER_50, True, "20 لكل 50 ورقة."),
    ("punching", "تخريز", "0", PER_COPY, False, "السعر غير محدد: الخيار معطّل حتى يحدد المالك السعر."),
    ("bone_folder_300", "مصنف عظم 300 ميكرون", "25", PER_COPY, True, ""),
    ("bone_folder_150", "مصنف عظم 150 ميكرون", "20", PER_COPY, True, ""),
    ("none", "لا شيء", "0", PER_COPY, True, ""),
    (
        "artistic_binding",
        "تجليد فني",
        "25",
        PER_COPY,
        False,
        "25 لأصغر مقاس و5 زيادة لكل مقاس أكبر عبر A3 وB7 وA6 وB6 وA5 وB5 وA4 وB4. "
        "معطّل: ترتيب المقاسات غير واضح ويحتاج تأكيد المالك.",
    ),
]
DESIGN_PAPER: list[_Row] = [
    *_rows(
        ("card_130", "كرتون 130 غ", "20"),
        ("card_230", "كرتون 230 غ", "30"),
        ("photo_glossy_230", "ورق فوتو GLOSSY 230 غ لميع", "50"),
        ("photo_glossy_200", "ورق فوتو GLOSSY 200 غ لميع", "40"),
        ("photo_glossy_180", "ورق فوتو GLOSSY 180 غ لميع", "30"),
        ("photo_glossy_180_double", "ورق فوتو GLOSSY 180 غ لميع على الوجهين", "50"),
        ("photo_matte_180", "ورق فوتو MATTE 180 غ", "30"),
        ("photo_sticker_glossy_135", "ورق فوتو لاصق GLOSSY 135 غ", "50"),
        ("sticker_mob", "ورق لاصق MOB", "30"),
        ("colored_paper_80", "ورق ملون 80 غ", "5"),
        NEAM_PINHOLE,
        NEAM_BARK,
        CERTIFICATE,
        unit=PER_SHEET,
    )
]

CATALOG: dict[str, list[_Row]] = {
    S.FRONT_COVER: COVER,
    S.BACK_COVER: COVER,
    S.FRONT_TRANSPARENCY_REQUIRED: TRANSPARENCY_REQUIRED,
    S.FRONT_TRANSPARENCY_OPTIONAL: TRANSPARENCY_OPTIONAL,
    S.BACK_TRANSPARENCY_REQUIRED: TRANSPARENCY_REQUIRED,
    S.BACK_TRANSPARENCY_OPTIONAL: TRANSPARENCY_OPTIONAL,
    S.FINAL_DELIVERY: FINAL_DELIVERY,
    S.FINISHING: FINISHING,
    S.DESIGN_PAPER: DESIGN_PAPER,
}


def usd_price(syp: str) -> Decimal:
    return (Decimal(syp) / SYP_PER_USD).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class Command(BaseCommand):
    help = "Create the sectioned paper-option catalog (new SYP and USD). Idempotent: only missing rows are created."

    @transaction.atomic
    def handle(self, *args, **options):
        created_count = 0
        for section, rows in CATALOG.items():
            for index, (code, name, syp, unit, active, description) in enumerate(rows, start=1):
                for currency, price in (
                    (FinanceCurrency.SYP, Decimal(syp)),
                    (FinanceCurrency.USD, usd_price(syp)),
                ):
                    _, created = PrintExtraOption.objects.get_or_create(
                        section=section,
                        code=code,
                        currency=currency,
                        defaults={
                            "name": name,
                            "description": description,
                            "price": price,
                            "price_unit": unit,
                            "sort_order": index,
                            "is_active": active,
                        },
                    )
                    created_count += int(created)
        self.stdout.write(self.style.SUCCESS(f"Seeded paper options (created only what was missing): {created_count}"))
