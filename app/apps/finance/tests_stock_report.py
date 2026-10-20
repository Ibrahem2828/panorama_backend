from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.printing.models import PrintOrder, PrintOrderItem, PrintOrderStatus

from .models import InventoryItem, StockMovement

URL = "/api/v1/dashboard/finance/stock-report/"


def make_user(role, email, phone):
    return User.objects.create_user(
        full_name=email.split("@")[0], email=email, phone_number=phone, password="StrongPass123!", role=role
    )


def api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def owner(db):
    return make_user(UserRole.ADMIN, "report-owner@example.com", "+963944500001")


@pytest.fixture
def cashier(db):
    return make_user(UserRole.PRINT_STAFF, "report-cashier@example.com", "+963944500002")


def move(item, kind, quantity, when: tuple[int, int, int], *, cost=None, currency="", order=None):
    movement = StockMovement.objects.create(
        item=item,
        movement_type=kind,
        quantity=Decimal(quantity),
        quantity_after=Decimal("0"),
        unit_cost=Decimal(cost) if cost is not None else None,
        currency=currency,
        order=order,
    )
    stamp = timezone.make_aware(datetime(when[0], when[1], when[2], 12, 0))
    StockMovement.objects.filter(pk=movement.pk).update(created_at=stamp)
    return movement


@pytest.fixture
def september(db):
    """An A4 ream used a bit and bought once in September 2026, with a purchase before and a use after the month."""

    paper = InventoryItem.objects.create(
        name="ورق A4", category="paper", unit="رزمة", quantity=Decimal("20"), reorder_level=Decimal("5")
    )
    shop = make_user(UserRole.NORMAL_USER, "report-customer@example.com", "+963944500003")
    order = PrintOrder.objects.create(
        user=shop, status=PrintOrderStatus.DELIVERED, total_price=Decimal("100"), currency="SYP"
    )
    PrintOrder.objects.filter(pk=order.pk).update(completed_at=timezone.make_aware(datetime(2026, 9, 10, 9, 0)))
    PrintOrderItem.objects.create(order=order, pages_count=7, copies=1, sheets_count=7, price=Decimal("100"))
    move(paper, "purchase", "20", (2026, 8, 20))  # before the month
    move(paper, "purchase", "10", (2026, 9, 5), cost="4", currency="USD")
    move(paper, "usage", "-3", (2026, 9, 10), order=order)  # automatic, by a delivered order
    move(paper, "usage", "-2", (2026, 9, 12))  # by hand
    move(paper, "waste", "-1", (2026, 9, 20))
    move(paper, "adjustment", "0.5", (2026, 9, 25))
    move(paper, "usage", "-4.5", (2026, 10, 3))  # after the month
    return paper


def row(data, name):
    return next(item for item in data["items"] if item["name"] == name)


@pytest.mark.django_db
def test_a_past_month_balances_from_the_ledger(owner, september):
    data = api(owner).get(URL, {"month": "2026-09"}).json()["data"]
    paper = row(data, "ورق A4")
    assert Decimal(str(paper["opening"])) == Decimal("20.00")  # what the shelf held on 1 September
    assert Decimal(str(paper["closing"])) == Decimal("24.50")  # today's 20 minus October's -4.5
    assert Decimal(str(paper["purchased"])) == Decimal("10.00") and paper["purchase_cost"] == {"USD": "40.00"}
    assert (
        Decimal(str(paper["used"])),
        Decimal(str(paper["used_by_orders"])),
        Decimal(str(paper["used_by_hand"])),
    ) == (Decimal("5.00"), Decimal("3.00"), Decimal("2.00"))
    assert Decimal(str(paper["waste"])) == Decimal("1.00") and Decimal(str(paper["adjustment"])) == Decimal("0.50")
    assert Decimal(str(paper["opening"])) + Decimal(str(paper["purchased"])) - Decimal(str(paper["used"])) - Decimal(
        str(paper["waste"])
    ) + Decimal(str(paper["adjustment"])) == Decimal(str(paper["closing"]))
    assert data["month"] == "2026-09" and data["days_elapsed"] == 30
    assert (
        data["totals"]["purchase_cost"] == {"USD": "40.00"}
        and data["totals"]["orders_delivered"] == 1
        and data["totals"]["sheets_printed"] == 7
    )


@pytest.mark.django_db
def test_the_daily_rate_projects_how_long_stock_lasts_and_what_to_buy(owner, september):
    data = api(owner).get(URL, {"month": "2026-09"}).json()["data"]
    plenty = row(data, "ورق A4")
    assert Decimal(str(plenty["daily_usage"])) == Decimal("0.1667") and plenty["days_left"] == 147
    assert Decimal(str(plenty["suggested_purchase"])) == Decimal("0.00")  # 24.5 already covers the next 30 days
    scarce = InventoryItem.objects.create(name="غلاف", category="other", unit="علبة", quantity=Decimal("1"))
    move(scarce, "usage", "-6", (2026, 9, 15))
    data = api(owner).get(URL, {"month": "2026-09"}).json()["data"]
    low = row(data, "غلاف")
    assert low["days_left"] == 5 and Decimal(str(low["suggested_purchase"])) == Decimal(
        "5.00"
    )  # 0.2/day x 30 = 6, minus the 1 left


@pytest.mark.django_db
def test_no_projection_without_a_few_days_of_data_or_any_usage(owner, db):
    fresh = InventoryItem.objects.create(name="جديد", unit="قطعة", quantity=Decimal("9"))
    move(fresh, "usage", "-1", (2026, 10, 1))
    idle = InventoryItem.objects.create(name="خامل", unit="قطعة", quantity=Decimal("9"))
    move(idle, "purchase", "1", (2026, 9, 3))
    with patch(
        "apps.finance.services.timezone.localdate", return_value=date(2026, 10, 2)
    ):  # the 2nd: only two days of data
        data = api(owner).get(URL, {"month": "2026-10"}).json()["data"]
    assert data["days_elapsed"] == 2
    assert row(data, "جديد")["daily_usage"] is None and row(data, "جديد")["suggested_purchase"] is None
    september_data = api(owner).get(URL, {"month": "2026-09"}).json()["data"]
    assert row(september_data, "خامل")["daily_usage"] is None  # bought, never used


@pytest.mark.django_db
def test_retired_items_appear_only_when_they_moved_in_the_month(owner, db):
    quiet = InventoryItem.objects.create(name="متقاعد", unit="قطعة", quantity=Decimal("0"), is_active=False)
    busy = InventoryItem.objects.create(name="متقاعد نشط سابقًا", unit="قطعة", quantity=Decimal("0"), is_active=False)
    move(busy, "usage", "-1", (2026, 9, 7))
    names = [item["name"] for item in api(owner).get(URL, {"month": "2026-09"}).json()["data"]["items"]]
    assert busy.name in names and quiet.name not in names


@pytest.mark.django_db
def test_the_month_is_validated_and_defaults_to_the_current_one(owner, db):
    client = api(owner)
    for bad in ("2026-13", "2026-9", "september", "2026-09-01"):
        assert client.get(URL, {"month": bad}).status_code == 400
    with patch("apps.finance.services.timezone.localdate", return_value=date(2026, 10, 20)):
        assert client.get(URL, {"month": "2026-11"}).status_code == 400  # not started yet
        data = client.get(URL).json()["data"]
    assert data["month"] == "2026-10" and data["period"]["to"] == "2026-10-31"


@pytest.mark.django_db
def test_only_people_who_manage_finance_can_read_the_report(owner, cashier, db):
    assert APIClient().get(URL).status_code == 401
    assert api(cashier).get(URL).status_code == 403  # purchase costs are finance data
    assert api(owner).get(URL).status_code == 200
