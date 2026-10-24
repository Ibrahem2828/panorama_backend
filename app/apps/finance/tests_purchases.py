"""Purchases & consumables: kinds, invoice upload, filters/CSV, reminders ("last bought N weeks ago"), waste reasons, ink alerts."""

from __future__ import annotations

import io
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.notifications.models import Notification

from .models import Expense, InventoryItem, PurchaseReminder, StockMovement
from .services import PurchaseReminderService, StockAlertService

EXPENSES = "/api/v1/dashboard/finance/expenses/"
REMINDERS = "/api/v1/dashboard/finance/purchase-reminders/"


def user(role, n):
    return User.objects.create_user(
        full_name=f"u{n}",
        email=f"purch{n}@example.com",
        phone_number=f"+96394448{n:04d}",
        password="StrongPass123!",
        role=role,
    )


def client(u) -> APIClient:
    c = APIClient()
    c.force_authenticate(u)
    return c


@pytest.fixture
def admin(db):
    return user(UserRole.ADMIN, 1)


def rows(response):
    data = response.json()["data"]
    return data["results"] if isinstance(data, dict) and "results" in data else data


def png(name="invoice.png"):
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(buffer, "PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def test_quick_add_with_kind_supplier_and_invoice(admin):
    response = client(admin).post(
        EXPENSES,
        {
            "description": "Cleaning supplies",
            "amount": "150.00",
            "currency": "SYP",
            "kind": "consumables",
            "supplier": "Al-Noor store",
            "notes": "monthly",
            "invoice": png(),
        },
        format="multipart",
    )
    assert response.status_code == 201, response.json()
    data = response.json()["data"]
    assert data["kind"] == "consumables" and data["supplier"] == "Al-Noor store" and data["has_invoice"] is True
    assert "invoice" not in data  # the file is never echoed back


def test_invoice_must_be_an_allowed_file(admin):
    bad = SimpleUploadedFile("invoice.png", b"not really an image", content_type="image/png")
    response = client(admin).post(
        EXPENSES, {"description": "x", "amount": "5", "currency": "SYP", "invoice": bad}, format="multipart"
    )
    assert response.status_code == 400


def test_filters_search_and_csv_export_neutralise_formulas(admin):
    api = client(admin)
    for description, kind, supplier in (
        ("Printer", "equipment", "TechCo"),
        ("Soap", "consumables", "Noor"),
        ('=HYPERLINK("http://evil")', "miscellaneous", "-cmd"),
    ):
        Expense.objects.create(description=description, amount=Decimal("10"), kind=kind, supplier=supplier)
    assert [r["description"] for r in rows(api.get(EXPENSES + "?kind=equipment"))] == ["Printer"]
    assert [r["description"] for r in rows(api.get(EXPENSES + "?search=noor"))] == ["Soap"]
    export = api.get(EXPENSES + "export/?kind=miscellaneous")
    assert export.status_code == 200 and export["Content-Type"].startswith("text/csv")
    body = export.content.decode("utf-8-sig")
    assert "'=HYPERLINK" in body and "'-cmd" in body
    assert "Printer" not in body


def test_export_needs_finance_permission(db):
    assert client(user(UserRole.PRINT_STAFF, 2)).get(EXPENSES + "export/").status_code == 403


def test_monthly_purchases_report_groups_by_kind_and_currency(admin):
    Expense.objects.create(description="a", amount=Decimal("100"), kind="purchases", currency="SYP")
    Expense.objects.create(description="b", amount=Decimal("5"), kind="purchases", currency="USD")
    Expense.objects.create(description="c", amount=Decimal("40"), kind="equipment", currency="SYP")
    data = client(admin).get("/api/v1/dashboard/finance/purchases-report/").json()["data"]
    assert data["by_kind"]["purchases"] == {"SYP": "100.00", "USD": "5.00"}
    assert data["by_kind"]["equipment"] == {"SYP": "40.00"}
    assert data["totals"]["SYP"] == "140.00" and data["count"] == 3


def test_reminder_reports_last_purchase_and_due_state(admin):
    ink = PurchaseReminder.objects.create(name="حبر", interval_weeks=10)
    assert PurchaseReminderService.status(ink)["last_purchase"] is None
    assert PurchaseReminderService.status(ink)["is_due"] is False  # counting starts at the first purchase
    Expense.objects.create(
        description="ink", amount=Decimal("90"), reminder=ink, spent_on=date.today() - timedelta(weeks=11)
    )
    info = PurchaseReminderService.status(ink)
    assert info["weeks_since"] == 11 and info["is_due"] is True
    listed = rows(client(admin).get(REMINDERS))[0]
    assert listed["name"] == "حبر" and listed["is_due"] is True and listed["weeks_since"] == 11


def test_due_reminder_is_announced_once_per_cycle_and_rearms_after_a_new_purchase(admin):
    ink = PurchaseReminder.objects.create(name="حبر", interval_weeks=2)
    Expense.objects.create(
        description="ink", amount=Decimal("90"), reminder=ink, spent_on=date.today() - timedelta(weeks=3)
    )
    assert PurchaseReminderService.notify_due() == 1
    assert PurchaseReminderService.notify_due() == 0
    assert Notification.objects.filter(user=admin, data__kind="purchase_reminder").count() == 1
    Expense.objects.create(
        description="ink again", amount=Decimal("90"), reminder=ink, spent_on=date.today() - timedelta(weeks=3, days=-1)
    )
    ink.refresh_from_db()
    # A newer purchase that is still overdue starts a new cycle.
    ink.last_notified_at = timezone.now() - timedelta(weeks=5)
    ink.save()
    assert PurchaseReminderService.notify_due() == 1


def test_reminders_are_finance_only(db):
    assert client(user(UserRole.PRINT_STAFF, 3)).get(REMINDERS).status_code == 403


def test_waste_reason_is_stored_and_summed_in_the_stock_report(admin):
    paper = InventoryItem.objects.create(name="ورق", category="paper", unit="رزمة", quantity=Decimal("10"))
    api = client(admin)
    url = f"/api/v1/dashboard/printing/stock/{paper.pk}/movements/"
    assert (
        api.post(url, {"movement_type": "waste", "quantity": "1", "waste_reason": "jam"}, format="json").status_code
        == 201
    )
    assert (
        api.post(
            url, {"movement_type": "waste", "quantity": "0.5", "waste_reason": "damaged"}, format="json"
        ).status_code
        == 201
    )
    assert api.post(url, {"movement_type": "waste", "quantity": "0.25"}, format="json").status_code == 201
    assert (
        api.post(url, {"movement_type": "waste", "quantity": "1", "waste_reason": "bogus"}, format="json").status_code
        == 400
    )
    assert set(StockMovement.objects.filter(item=paper).values_list("waste_reason", flat=True)) == {
        "jam",
        "damaged",
        "",
    }
    report = api.get("/api/v1/dashboard/finance/stock-report/").json()["data"]
    row = next(item for item in report["items"] if item["id"] == paper.pk)
    assert Decimal(str(row["waste"])) == Decimal("1.75")
    split = {key: Decimal(str(value)) for key, value in row["waste_by_reason"].items()}
    assert split == {"jam": Decimal("1.00"), "damaged": Decimal("0.50"), "other": Decimal("0.25")}


def test_ink_never_triggers_low_stock_alerts(admin):
    ink = InventoryItem.objects.create(
        name="حبر", category="ink", unit="bottle", quantity=Decimal("2"), reorder_level=Decimal("2")
    )
    assert StockAlertService.kind(ink, Decimal("3"), Decimal("2")) is None
    assert StockAlertService.kind(ink, Decimal("1"), Decimal("0")) is None
    paper = InventoryItem.objects.create(
        name="ورق2", category="paper", unit="x", quantity=Decimal("2"), reorder_level=Decimal("2")
    )
    assert StockAlertService.kind(paper, Decimal("3"), Decimal("2")) == "low_stock"
