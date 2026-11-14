"""The sectioned paper-option catalog: seeding, the customer's options and quote/order, validation, dashboard CRUD."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.accounts.choices import UserRole
from apps.accounts.models import User

from .models import PrintExtraOption, PrintExtraSection, PrintOrder
from .tests_print_options import make_pdf

EXTRAS = "/api/v1/dashboard/printing/extra-options/"


def make_user(role, email, phone):
    return User.objects.create_user(
        full_name=email.split("@")[0], email=email, phone_number=phone, password="StrongPass123!", role=role
    )


def api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def customer(db):
    return make_user(UserRole.NORMAL_USER, "extras-customer@example.com", "+963944490001")


@pytest.fixture
def cashier(db):
    return make_user(UserRole.PRINT_STAFF, "extras-cashier@example.com", "+963944490002")


@pytest.fixture
def seeded(db):
    call_command("seed_print_finance_defaults")
    call_command("seed_print_paper_options")


def order_fields(extras, pages=12, copies=1):
    return {
        "items[0]uploaded_file": SimpleUploadedFile("doc.pdf", make_pdf(pages), content_type="application/pdf"),
        "items[0]copies": copies,
        "items[0]color_mode": "black_white",
        "items[0]paper_size": "A4",
        "items[0]sides": "one_sided",
        "items[0]binding": "none",
        "items[0]extras": json.dumps(extras),
    }


def quote(user, extras, **kwargs):
    return api(user).post("/api/v1/printing/quote/", order_fields(extras, **kwargs), format="multipart")


@pytest.mark.django_db
def test_seed_is_idempotent_and_never_overwrites_owner_edits(seeded):
    total = PrintExtraOption.objects.count()
    assert total == 120  # 60 options x (SYP + USD)
    card = PrintExtraOption.objects.get(section="front_cover", code="card_130", currency="SYP")
    assert card.price == Decimal("10")
    assert PrintExtraOption.objects.get(section="front_cover", code="card_130", currency="USD").price == Decimal("0.08")
    wire = PrintExtraOption.objects.get(section="finishing", code="wire_comb", currency="SYP")
    assert (wire.price, wire.price_unit) == (Decimal("20"), "per_50_sheets")
    assert not PrintExtraOption.objects.get(section="finishing", code="punching", currency="SYP").is_active
    PrintExtraOption.objects.filter(pk=card.pk).update(price=Decimal("12"), is_active=False)
    call_command("seed_print_paper_options")
    assert PrintExtraOption.objects.count() == total
    card.refresh_from_db()
    assert card.price == Decimal("12") and not card.is_active


@pytest.mark.django_db
def test_print_options_lists_every_section_with_prices_in_the_currency(seeded, customer):
    data = api(customer).get("/api/v1/printing/options/?currency=SYP").json()["data"]
    sections = {row["section"]: row for row in data["extras"]}
    assert set(sections) == set(PrintExtraSection.values)
    covers = {option["code"]: option for option in sections["front_cover"]["options"]}
    assert Decimal(str(covers["card_230"]["price"])) == Decimal("15")
    assert "punching" not in [option["code"] for option in sections["finishing"]["options"]]  # inactive
    usd = api(customer).get("/api/v1/printing/options/?currency=USD").json()["data"]
    usd_covers = {
        option["code"]: option for option in {row["section"]: row for row in usd["extras"]}["front_cover"]["options"]
    }
    assert usd["currency"] == "USD" and Decimal(str(usd_covers["card_230"]["price"])) == Decimal("0.13")


@pytest.mark.django_db
def test_quote_adds_the_chosen_options_per_unit(seeded, customer):
    # 12 sheets x 4 = 48 per copy; front cover 10 + back cover 5 + comb 20 (one block of 50 sheets) per copy
    response = quote(customer, {"front_cover": "card_130", "back_cover": "paper", "finishing": "wire_comb"}, copies=2)
    assert response.status_code == 200, response.json()
    data = response.json()["data"]
    assert Decimal(str(data["exact_total"])) == Decimal("96") + 2 * (10 + 5 + 20)
    snapshot = data["items"][0]["pricing_snapshot"]
    assert {line["section"] for line in snapshot["extras"]} == {"front_cover", "back_cover", "finishing"}
    assert Decimal(snapshot["extras_price"]) == Decimal("70")


@pytest.mark.django_db
def test_per_50_sheets_and_per_sheet_units(seeded, customer):
    comb = quote(customer, {"finishing": "wire_comb"}, pages=120)
    assert Decimal(str(comb.json()["data"]["exact_total"])) == Decimal("480") + 60  # ceil(120 / 50) = 3 blocks x 20
    paper = quote(customer, {"design_paper": "card_130"}, pages=3, copies=2)
    assert Decimal(str(paper.json()["data"]["exact_total"])) == Decimal("24") + 3 * 2 * 20  # per sheet x copies


@pytest.mark.django_db
def test_unknown_inactive_or_misplaced_codes_are_rejected_in_arabic(seeded, customer):
    for extras in (
        {"front_cover": "ghost"},
        {"finishing": "punching"},  # inactive until the owner sets a price
        {"front_cover": "transparency_150"},  # exists, but in another section
        {"no_such_section": "x"},
    ):
        response = quote(customer, extras)
        assert response.status_code == 400, extras
        assert any("؀" <= ch <= "ۿ" for ch in json.dumps(response.json(), ensure_ascii=False)), extras
    assert quote(customer, {"front_cover": ""}).status_code == 200  # an empty choice means none


@pytest.mark.django_db
def test_the_order_stores_the_choices_and_the_price(seeded, customer):
    response = api(customer).post(
        "/api/v1/printing/orders/",
        order_fields({"front_cover": "card_230", "final_delivery": "bag"}),
        format="multipart",
    )
    assert response.status_code == 201, response.json()
    item = response.json()["data"]["items"][0]
    assert item["extras"] == {"front_cover": "card_230", "final_delivery": "bag"}
    assert Decimal(str(item["extras_price"])) == Decimal("15")
    assert PrintOrder.objects.get().total_price == Decimal("70.00")  # 48 + 15 = 63, rounded to tens (half up)


@pytest.mark.django_db
def test_without_extras_nothing_changes(seeded, customer):
    response = quote(customer, {})
    assert Decimal(str(response.json()["data"]["exact_total"])) == Decimal("48")


@pytest.mark.django_db
def test_dashboard_crud_needs_the_printing_capability(seeded, customer, cashier):
    payload = {
        "section": "front_cover",
        "code": "gold",
        "name": "ذهبي",
        "price": "40",
        "price_unit": "per_copy",
        "currency": "SYP",
    }
    assert api(customer).post(EXTRAS, payload, format="json").status_code == 403
    assert api(customer).get(EXTRAS).status_code == 403
    created = api(cashier).post(EXTRAS, payload, format="json")
    assert created.status_code == 201, created.json()
    row_id = created.json()["data"]["id"]
    assert api(cashier).post(EXTRAS, payload, format="json").status_code == 400  # unique per section/code/currency
    assert api(cashier).post(EXTRAS, {**payload, "code": "neg", "price": "-1"}, format="json").status_code == 400
    assert api(cashier).patch(f"{EXTRAS}{row_id}/", {"price": "45"}, format="json").status_code == 200
    filtered = api(cashier).get(f"{EXTRAS}?section=front_cover&currency=SYP").json()["data"]
    assert any(row["code"] == "gold" and Decimal(str(row["price"])) == Decimal("45") for row in filtered)
    assert api(cashier).delete(f"{EXTRAS}{row_id}/").status_code in (200, 204)
    assert not PrintExtraOption.objects.filter(pk=row_id, is_deleted=False).exists()


@pytest.mark.django_db
def test_counter_quote_accepts_the_options(seeded, cashier):
    response = api(cashier).post(
        "/api/v1/dashboard/printing/counter-orders/quote/",
        {
            "items": [
                {
                    "pages_count": 10,
                    "copies": 1,
                    "color_mode": "black_white",
                    "paper_size": "A4",
                    "sides": "one_sided",
                    "extras": {"back_cover": "card_130"},
                }
            ]
        },
        format="json",
    )
    assert response.status_code == 200, response.json()
    assert Decimal(str(response.json()["data"]["exact_total"])) == Decimal("50")
