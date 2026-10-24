"""Stationery quick-sale: sellable items, sale, stock deduction, report; counter orders for ready products."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.audit.models import AuditAction, AuditLog
from apps.finance.models import InventoryItem
from apps.printing.models import PrintOrder, ReadyProduct


def make_user(role, email, phone):
    return User.objects.create_user(
        full_name=email.split("@")[0], email=email, phone_number=phone, password="StrongPass123!", role=role
    )


def api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def cashier(db):
    return make_user(UserRole.PRINT_STAFF, "shop-cashier@example.com", "+963944500001")


@pytest.fixture
def stock(db):
    pen = InventoryItem.objects.create(
        name="قلم أزرق",
        category="pens",
        unit="piece",
        quantity=Decimal("50"),
        is_sellable=True,
        sale_price=Decimal("500"),
    )
    notebook = InventoryItem.objects.create(
        name="دفتر 100 ورقة",
        category="pens",
        unit="piece",
        quantity=Decimal("10"),
        is_sellable=True,
        sale_price=Decimal("3500"),
    )
    hidden = InventoryItem.objects.create(
        name="حبر", category="ink", unit="bottle", quantity=Decimal("4"), sale_price=Decimal("9")
    )
    return {"pen": pen, "notebook": notebook, "hidden": hidden}


def rows(response):
    data = response.json()["data"]
    return data["results"] if isinstance(data, dict) and "results" in data else data


def sell(client, lines, **extra):
    return client.post("/api/v1/dashboard/shop/sales/create/", {"lines": lines, **extra}, format="json")


@pytest.mark.django_db
def test_only_items_marked_for_sale_are_listed(cashier, stock):
    names = {row["name"] for row in rows(api(cashier).get("/api/v1/dashboard/shop/items/"))}
    assert names == {"قلم أزرق", "دفتر 100 ورقة"}


@pytest.mark.django_db
def test_a_sale_totals_the_lines_and_takes_the_stock(cashier, stock):
    response = sell(
        api(cashier),
        [{"item": stock["pen"].pk, "quantity": "3"}, {"item": stock["notebook"].pk, "quantity": "2"}],
        payment_method="cash",
    )
    assert response.status_code == 201, response.json()
    data = response.json()["data"]
    assert Decimal(data["total"]) == Decimal("8500.00") and data["currency"] == "SYP" and len(data["lines"]) == 2
    stock["pen"].refresh_from_db()
    stock["notebook"].refresh_from_db()
    assert stock["pen"].quantity == Decimal("47.00") and stock["notebook"].quantity == Decimal("8.00")
    assert AuditLog.objects.filter(action=AuditAction.STATIONERY_SALE_RECORDED).exists()


@pytest.mark.django_db
def test_a_sale_is_all_or_nothing(cashier, stock):
    client = api(cashier)
    not_enough = sell(
        client, [{"item": stock["pen"].pk, "quantity": "1"}, {"item": stock["notebook"].pk, "quantity": "11"}]
    )
    assert not_enough.status_code == 400
    stock["pen"].refresh_from_db()
    assert stock["pen"].quantity == Decimal("50.00")
    assert sell(client, [{"item": stock["hidden"].pk, "quantity": "1"}]).status_code == 400
    assert sell(client, []).status_code == 400
    assert sell(client, [{"item": stock["pen"].pk, "quantity": "0"}]).status_code == 400


@pytest.mark.django_db
def test_replaying_the_same_idempotency_key_sells_once(cashier, stock):
    client = api(cashier)
    body = {"lines": [{"item": stock["pen"].pk, "quantity": "1"}]}
    first = client.post(
        "/api/v1/dashboard/shop/sales/create/", body, format="json", HTTP_IDEMPOTENCY_KEY="sale-key-0001"
    )
    second = client.post(
        "/api/v1/dashboard/shop/sales/create/", body, format="json", HTTP_IDEMPOTENCY_KEY="sale-key-0001"
    )
    assert first.status_code == second.status_code == 201
    stock["pen"].refresh_from_db()
    assert stock["pen"].quantity == Decimal("49.00")


@pytest.mark.django_db
def test_report_and_list(cashier, stock):
    client = api(cashier)
    sell(client, [{"item": stock["pen"].pk, "quantity": "4"}])
    sell(client, [{"item": stock["pen"].pk, "quantity": "1"}, {"item": stock["notebook"].pk, "quantity": "1"}])
    report = client.get("/api/v1/dashboard/shop/sales/report/").json()["data"]
    assert Decimal(str(report["totals_by_currency"]["SYP"]["total"])) == Decimal("6000.00")
    assert report["totals_by_currency"]["SYP"]["sales"] == 2
    top = report["top_items"][0]
    assert top["name"] == "دفتر 100 ورقة" or Decimal(str(top["revenue"])) >= Decimal("2500")
    assert len(rows(client.get("/api/v1/dashboard/shop/sales/"))) == 2
    sale_id = rows(client.get("/api/v1/dashboard/shop/sales/"))[0]["id"]
    assert client.get(f"/api/v1/dashboard/shop/sales/{sale_id}/").status_code == 200


@pytest.mark.django_db
def test_students_cannot_sell(stock):
    student = make_user(UserRole.NORMAL_USER, "shop-normal@example.com", "+963944500002")
    assert sell(api(student), [{"item": stock["pen"].pk, "quantity": "1"}]).status_code == 403


@pytest.mark.django_db
def test_counter_order_for_a_ready_product(cashier):
    call_command("seed_print_finance_defaults")
    product = ReadyProduct.objects.create(
        name="دفتر تشريح", pages_count=80, sides="double_sided", binding="spiral", price=Decimal("4000"), currency="SYP"
    )
    client = api(cashier)
    line = {"product": product.pk, "copies": 2}
    quote = client.post("/api/v1/dashboard/printing/counter-orders/quote/", {"items": [line]}, format="json")
    assert quote.status_code == 200, quote.json()
    assert Decimal(str(quote.json()["data"]["suggested_total"])) == Decimal("8000")
    created = client.post(
        "/api/v1/dashboard/printing/counter-orders/", {"items": [line], "customer_name": "سارة"}, format="json"
    )
    assert created.status_code == 201, created.json()
    item = PrintOrder.objects.get(pk=created.json()["data"]["id"]).items.get()
    assert item.product_id == product.pk and item.binding == "spiral" and item.pages_count == 80
    assert item.original_file_name == "دفتر تشريح"
