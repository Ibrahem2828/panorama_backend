"""Print-dialog options (pages per sheet, two-sided, page range, collation), configurable binding/cover/wire pricing and 4.9
(customer options never depend on the stock)."""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from pypdf import PdfWriter
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.finance.models import ConsumptionKey, InventoryItem

from .models import (
    BindingKind,
    BindingPriceMode,
    PrintBindingPrice,
    PrintCoverOption,
    PrintOrder,
    SpiralWireSpec,
)
from .options import parse_page_range, sheet_layout, sheets_per_copy


def make_pdf(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest.fixture
def customer(db):
    return User.objects.create_user(
        full_name="Options Customer",
        email="options-customer@example.com",
        phone_number="+963944480001",
        password="StrongPass123!",
        role=UserRole.NORMAL_USER,
    )


@pytest.fixture
def prices(db):
    call_command("seed_print_finance_defaults")


def api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def quote(user, pages=12, **item):
    fields = {
        "items[0]uploaded_file": SimpleUploadedFile("doc.pdf", make_pdf(pages), content_type="application/pdf"),
        "items[0]copies": 1,
        "items[0]color_mode": "black_white",
        "items[0]paper_size": "A4",
        "items[0]sides": "one_sided",
        "items[0]binding": "none",
    }
    for key, value in item.items():
        fields[f"items[0]{key}"] = value
    return api(user).post("/api/v1/printing/quote/", fields, format="multipart")


def money(response) -> Decimal:
    return Decimal(str(response.json()["data"]["exact_total"]))


# ---- pure option logic ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "total", "expected"),
    [
        ("", 5, [1, 2, 3, 4, 5]),
        ("  ", 3, [1, 2, 3]),
        ("1-5, 8", 10, [1, 2, 3, 4, 5, 8]),
        ("3,1,3", 5, [3, 1]),
        ("2-2", 5, [2]),
        ("1–3؛ 5".replace("؛", ";"), 5, [1, 2, 3, 5]),
        ("4،6", 6, [4, 6]),
        ("٣-٥", 6, [3, 4, 5]),  # Arabic-Indic digits are fine
    ],
)
def test_page_range_parsing(text, total, expected):
    assert parse_page_range(text, total) == expected


@pytest.mark.parametrize("text", ["0", "5-3", "1-99", "abc", "1-", "-3", "1,,x"])
def test_bad_page_ranges_are_rejected(text):
    with pytest.raises(ValidationError):
        parse_page_range(text, 10)


@pytest.mark.parametrize(
    ("per_sheet", "orientation", "cols", "rows", "landscape"),
    [
        (1, "auto", 1, 1, False),
        (2, "auto", 2, 1, True),
        (4, "auto", 2, 2, False),
        (6, "auto", 3, 2, True),
        (9, "auto", 3, 3, False),
        (16, "auto", 4, 4, False),
        (2, "portrait", 1, 2, False),
        (4, "landscape", 2, 2, True),
        (6, "portrait", 2, 3, False),
    ],
)
def test_sheet_layouts(per_sheet, orientation, cols, rows, landscape):
    layout = sheet_layout(per_sheet, orientation)
    assert (layout.columns, layout.rows, layout.landscape) == (cols, rows, landscape)


def test_unknown_pages_per_sheet_is_rejected():
    with pytest.raises(ValidationError):
        sheet_layout(3)


@pytest.mark.parametrize(
    ("pages", "per_sheet", "double", "expected"),
    [
        (10, 1, False, 10),
        (10, 1, True, 5),
        (11, 1, True, 6),
        (12, 4, True, 2),
        (13, 4, True, 2),
        (17, 4, True, 3),
        (1, 16, True, 1),
    ],
)
def test_sheets_per_copy(pages, per_sheet, double, expected):
    assert sheets_per_copy(pages, per_sheet, double) == expected


# ---- quote through the API ------------------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_old_style_request_is_priced_as_before(prices, customer):
    response = quote(customer, pages=5, copies=2)
    assert response.status_code == 200
    assert money(response) == Decimal("40")  # 5 pages x 2 copies x 4
    item = response.json()["data"]["items"][0]
    assert item["sheets_count"] == 10


@pytest.mark.django_db
def test_four_pages_per_sheet_two_sided_uses_an_eighth_of_the_sheets(prices, customer):
    response = quote(customer, pages=12, copies=3, pages_per_sheet=4, sides="double_sided")
    assert response.status_code == 200, response.json()
    item = response.json()["data"]["items"][0]
    assert item["sheets_count"] == 6  # ceil(12 / 4 / 2) = 2 sheets per copy x 3 copies
    snapshot = item["pricing_snapshot"]
    assert snapshot["layout"]["columns"] == 2 and snapshot["layout"]["rows"] == 2
    assert snapshot["selected_pages"] == 12 and snapshot["pages_per_sheet"] == 4


@pytest.mark.django_db
def test_page_range_limits_the_pages_that_are_charged(prices, customer):
    response = quote(customer, pages=12, page_range="1-5, 8")
    assert money(response) == Decimal("24")  # 6 pages x 4
    assert quote(customer, pages=12, page_range="1-30").status_code == 400
    assert quote(customer, pages=12, page_range="x").status_code == 400


@pytest.mark.django_db
def test_unknown_pages_per_sheet_value_is_refused_by_the_api(prices, customer):
    assert quote(customer, pages_per_sheet=3).status_code == 400


@pytest.mark.django_db
def test_order_stores_the_options_and_replaying_the_key_with_other_options_is_refused(prices, customer):
    client = api(customer)

    def post(**extra):
        data = {
            "items[0]uploaded_file": SimpleUploadedFile("doc.pdf", make_pdf(8), content_type="application/pdf"),
            "items[0]copies": 1,
            "items[0]color_mode": "black_white",
            "items[0]paper_size": "A4",
            "items[0]sides": "double_sided",
            "items[0]binding": "none",
            "items[0]pages_per_sheet": 2,
            "items[0]duplex_flip": "short_edge",
            "items[0]collate": "false",
            **extra,
        }
        return client.post("/api/v1/printing/orders/", data, format="multipart", HTTP_IDEMPOTENCY_KEY="opt-key-0001")

    first = post()
    assert first.status_code == 201, first.json()
    item = PrintOrder.objects.get(pk=first.json()["data"]["id"]).items.get()
    assert (item.pages_per_sheet, item.duplex_flip, item.collate, item.selected_pages) == (2, "short_edge", False, 8)
    assert item.sheets_count == 2  # ceil(8 / 2 / 2)
    assert post(**{"items[0]pages_per_sheet": 4}).status_code == 409


# ---- binding types, wire by weight, covers --------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_wire_priced_by_weight_uses_the_owners_table(prices, customer):
    PrintBindingPrice.objects.update_or_create(
        binding="spiral",
        currency="SYP",
        defaults={
            "kind": BindingKind.SPIRAL,
            "price_mode": BindingPriceMode.PER_WIRE_KG,
            "price_per_kg": Decimal("60000"),
        },
    )
    # 20 sheets one-sided needs a row: without it the binding cannot be priced and the customer is told so
    assert quote(customer, pages=20, binding="spiral").status_code == 400
    SpiralWireSpec.objects.create(min_sheets=11, max_sheets=30, weight_grams=Decimal("25"))
    response = quote(customer, pages=20, copies=2, binding="spiral")
    assert response.status_code == 200, response.json()
    item = response.json()["data"]["items"][0]
    assert Decimal(str(item["binding_price"])) == Decimal("1500.00")  # 25 g x 60000 per kg
    assert item["pricing_snapshot"]["wire_grams_per_copy"] == "25.00"
    assert money(response) == Decimal("160") + Decimal("3000")  # 40 sheets x 4 + 2 x 1500


@pytest.mark.django_db
def test_custom_binding_type_and_cover_are_priced_and_validated(prices, customer):
    PrintBindingPrice.objects.create(
        binding="hard_cover", name="تجليد كرتوني", kind=BindingKind.OTHER, price_per_copy=Decimal("500"), currency="SYP"
    )
    PrintCoverOption.objects.create(
        code="navy", name="كحلي", color_hex="#1E3A8A", price_per_copy=Decimal("100"), currency="SYP"
    )
    response = quote(customer, pages=5, binding="hard_cover", cover="navy", copies=2)
    assert response.status_code == 200, response.json()
    assert money(response) == Decimal("40") + Decimal("1000") + Decimal("200")
    assert quote(customer, pages=5, binding="not_offered").status_code == 400
    assert quote(customer, pages=5, cover="ghost").status_code == 400


# ---- 4.9 the customer's options never depend on the stock ----------------------------------------------------------------------


@pytest.mark.django_db
def test_prices_and_options_do_not_depend_on_the_stock(prices, customer):
    before = money(quote(customer, pages=6, binding="spiral"))
    InventoryItem.objects.all().update(quantity=Decimal("0"), is_active=False)
    assert money(quote(customer, pages=6, binding="spiral")) == before
    InventoryItem.objects.all().delete()
    response = quote(customer, pages=6, binding="spiral")
    assert response.status_code == 200 and money(response) == before


@pytest.mark.django_db
def test_wire_is_taken_from_the_shelf_in_grams(prices, customer):
    from apps.printing.models import PrintOrderStatus
    from apps.printing.services import PrintOrderService, PrintStatusService

    PrintBindingPrice.objects.update_or_create(
        binding="spiral", currency="SYP", defaults={"kind": BindingKind.SPIRAL, "price_per_copy": Decimal("72")}
    )
    SpiralWireSpec.objects.create(min_sheets=1, max_sheets=50, weight_grams=Decimal("20"))
    wire = InventoryItem.objects.create(
        name="سلك تسليك",
        category="other",
        unit="kg",
        quantity=Decimal("5"),
        consumption_key=ConsumptionKey.SPIRAL_WIRE,
        consumption_per_use=Decimal("0.001"),
    )
    staff = User.objects.create_user(
        full_name="Staff",
        email="options-staff@example.com",
        phone_number="+963944480002",
        password="StrongPass123!",
        role=UserRole.ADMIN,
    )
    order = PrintOrderService.create_order(
        customer,
        [
            {
                "uploaded_file": SimpleUploadedFile("d.pdf", make_pdf(10), content_type="application/pdf"),
                "copies": 3,
                "color_mode": "black_white",
                "paper_size": "A4",
                "sides": "one_sided",
                "binding": "spiral",
            }
        ],
    )
    for status in ("under_review", "accepted", "printing", "ready", "delivered"):
        PrintStatusService.change_status(order, getattr(PrintOrderStatus, status.upper()), staff)
    wire.refresh_from_db()
    assert wire.quantity == Decimal("4.94")  # 3 copies x 20 g = 60 g = 0.06 kg
