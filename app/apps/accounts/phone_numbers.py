from __future__ import annotations

import re

from rest_framework import serializers


INVALID_PHONE_MESSAGE = "صيغة رقم الجوال غير صحيحة. استخدم مثالاً مثل: +963994109259 أو 0994109259."
EXPECTED_PHONE_FORMAT = "E.164"
PHONE_EXAMPLES = ["+963994109259", "0994109259"]

_ARABIC_DIGIT_TRANSLATION = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
    "01234567890123456789",
)


def clean_phone_number(value: str) -> str:
    return re.sub(r"[\s-]+", "", str(value or "").translate(_ARABIC_DIGIT_TRANSLATION))


def normalize_phone_number(value: str) -> str:
    phone_number = clean_phone_number(value)
    if re.fullmatch(r"09\d{8}", phone_number):
        return f"+963{phone_number[1:]}"
    if re.fullmatch(r"9639\d{8}", phone_number):
        return f"+{phone_number}"
    if re.fullmatch(r"\+9639\d{8}", phone_number):
        return phone_number
    raise serializers.ValidationError(INVALID_PHONE_MESSAGE, code="invalid_phone")


def normalize_phone_number_or_none(value: str) -> str | None:
    try:
        return normalize_phone_number(value)
    except serializers.ValidationError:
        return None
