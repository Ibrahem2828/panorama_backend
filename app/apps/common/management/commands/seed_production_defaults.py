from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand

from apps.feedback.models import FeedbackPromptPolicy
from apps.printing.models import (
    PrintBinding,
    PrintBindingPrice,
    PrintColorMode,
    PrintPaperSize,
    PrintPickupLocation,
    PrintPricingRule,
    PrintSides,
)

PROMPT_POLICIES = [
    ("registration", "registration.completed", "تجربة إنشاء الحساب", "كيف تقيّم سهولة إنشاء حسابك؟", 30, 35),
    ("verification", "verification.submitted", "طلب التوثيق", "كيف تقيّم خطوات إرسال طلب التوثيق؟", 30, 40),
    ("verification", "verification.reviewed", "نتيجة التوثيق", "كيف تقيّم تجربة التوثيق كاملة؟", 45, 60),
    ("subject", "subject.opened", "المواد الدراسية", "هل وصلت إلى المادة التي تبحث عنها بسهولة؟", 30, 20),
    ("group", "group.joined", "الانضمام إلى الغروب", "كيف تقيّم تجربة الانضمام إلى الغروب؟", 30, 35),
    ("chat", "chat.session.completed", "محادثات الغروبات", "كيف تقيّم سرعة وسهولة المحادثة؟", 14, 20),
    ("file", "file.viewed", "عرض الملفات", "كيف تقيّم تجربة فتح وقراءة الملف؟", 21, 25),
    ("printing", "printing.quote.completed", "تسعير الطباعة", "هل كانت خيارات الطباعة والسعر واضحة؟", 30, 40),
    ("printing", "printing.order.created", "طلب الطباعة", "كيف تقيّم سهولة إنشاء طلب الطباعة؟", 30, 50),
    ("printing", "printing.order.delivered", "استلام الطباعة", "كيف تقيّم تجربة الطباعة والاستلام؟", 45, 100),
    ("support", "support.ticket.resolved", "الدعم الفني", "كيف تقيّم حل المشكلة وسرعة الدعم؟", 45, 100),
    ("external_channel", "group.whatsapp.opened", "القناة المساندة", "هل كان الوصول إلى قناة الغروب واضحًا؟", 30, 15),
    ("search", "search.completed", "البحث", "هل وجدت ما تبحث عنه؟", 14, 20),
    ("app", "app.general", "تجربتك مع بانوراما", "ما تقييمك العام لتطبيق بانوراما؟", 60, 100),
]


class Command(BaseCommand):
    help = "Seed idempotent feedback policies and optional production print defaults."

    def handle(self, *args, **options):
        for context, action_key, title, question, cooldown, sample in PROMPT_POLICIES:
            FeedbackPromptPolicy.objects.update_or_create(
                context=context, action_key=action_key,
                defaults={
                    "title": title, "question": question, "cooldown_days": cooldown,
                    "sample_percent": sample, "allow_comment": True,
                    "allow_suggestion": True, "is_active": True, "is_deleted": False,
                },
            )
        self.stdout.write(self.style.SUCCESS(f"Feedback prompt policies ready: {len(PROMPT_POLICIES)}"))

        location_name = os.environ.get("DEFAULT_PICKUP_LOCATION_NAME", "").strip()
        if location_name:
            PrintPickupLocation.objects.update_or_create(
                name=location_name,
                defaults={
                    "address": os.environ.get("DEFAULT_PICKUP_LOCATION_ADDRESS", "").strip(),
                    "instructions": os.environ.get("DEFAULT_PICKUP_LOCATION_INSTRUCTIONS", "").strip(),
                    "is_active": True, "is_deleted": False,
                },
            )
            self.stdout.write(self.style.SUCCESS("Default pickup location configured."))

        self._seed_optional_pricing()

    def _decimal_env(self, name):
        raw = os.environ.get(name, "").strip()
        if not raw:
            return None
        try:
            value = Decimal(raw)
        except InvalidOperation as exc:
            raise ValueError(f"{name} must be a valid decimal") from exc
        if value < 0:
            raise ValueError(f"{name} cannot be negative")
        return value

    def _seed_optional_pricing(self):
        specs = [
            ("DEFAULT_PRINT_PRICE_BW_A4_ONE", PrintColorMode.BLACK_WHITE, PrintPaperSize.A4, PrintSides.ONE_SIDED),
            ("DEFAULT_PRINT_PRICE_BW_A4_DOUBLE", PrintColorMode.BLACK_WHITE, PrintPaperSize.A4, PrintSides.DOUBLE_SIDED),
            ("DEFAULT_PRINT_PRICE_COLOR_A4_ONE", PrintColorMode.COLOR, PrintPaperSize.A4, PrintSides.ONE_SIDED),
            ("DEFAULT_PRINT_PRICE_COLOR_A4_DOUBLE", PrintColorMode.COLOR, PrintPaperSize.A4, PrintSides.DOUBLE_SIDED),
        ]
        currency = os.environ.get("PRINT_CURRENCY", "SYP").strip() or "SYP"
        count = 0
        for env_name, color, size, sides in specs:
            value = self._decimal_env(env_name)
            if value is None:
                continue
            PrintPricingRule.objects.update_or_create(
                name=env_name,
                defaults={
                    "color_mode": color, "paper_size": size, "sides": sides,
                    "price_per_sheet": value, "setup_fee": Decimal("0"),
                    "currency": currency, "is_active": True, "is_deleted": False,
                },
            )
            count += 1
        for binding in PrintBinding.values:
            env_name = f"DEFAULT_BINDING_PRICE_{binding.upper()}"
            value = self._decimal_env(env_name)
            if value is None and binding == PrintBinding.NONE:
                value = Decimal("0")
            if value is not None:
                PrintBindingPrice.objects.update_or_create(
                    binding=binding,
                    defaults={"price_per_copy": value, "currency": currency, "is_active": True, "is_deleted": False},
                )
        self.stdout.write(self.style.SUCCESS(f"Optional print pricing rules configured: {count}"))
