from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.choices import UserRole
from apps.accounts.models import User, UserPermissionOverride
from apps.audit.models import AuditAction, AuditLog
from apps.common.tests_phase3 import PNG_BYTES
from apps.printing.models import PrintOrder, PrintOrderItem, PrintOrderStatus, PrintPricingRule

from .models import ExchangeRate, Expense, InventoryItem, OrderPayment, StockMovement


def make_user(role, email, phone):
    return User.objects.create_user(
        full_name=email.split("@")[0], email=email, phone_number=phone, password="StrongPass123!", role=role
    )


@pytest.fixture
def admin(db):
    return make_user(UserRole.ADMIN, "fin-admin@example.com", "+963944460001")


@pytest.fixture
def cashier(db):
    return make_user(UserRole.PRINT_STAFF, "cashier@example.com", "+963944460002")


@pytest.fixture
def customer(db):
    return make_user(UserRole.NORMAL_USER, "customer@example.com", "+963944460003")


def api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def delivered_order(user, total, currency="SYP", days_ago=0, pages=10):
    order = PrintOrder.objects.create(
        user=user,
        status=PrintOrderStatus.DELIVERED,
        total_price=Decimal(total),
        currency=currency,
        completed_at=timezone.now() - timedelta(days=days_ago),
    )
    PrintOrderItem.objects.create(order=order, pages_count=pages, copies=1, sheets_count=pages, price=Decimal(total))
    return order


def quote_request(client, currency=None, binding="spiral"):
    upload = SimpleUploadedFile("a.png", PNG_BYTES, content_type="image/png")
    data = {
        "items[0]uploaded_file": upload,
        "items[0]copies": 2,
        "items[0]color_mode": "black_white",
        "items[0]paper_size": "A4",
        "items[0]sides": "one_sided",
        "items[0]binding": binding,
    }
    if currency:
        data["currency"] = currency
    return client.post("/api/v1/printing/quote/", data, format="multipart")


# ---- seed + two-currency pricing --------------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_is_idempotent_and_never_overwrites_owner_edits():
    call_command("seed_print_finance_defaults")
    rule = PrintPricingRule.objects.get(name__startswith="A4 - أبيض وأسود - وجه واحد - SYP")
    rule.price_per_sheet = Decimal("9.99")
    rule.save()
    item = InventoryItem.objects.get(name="ورق A4 - 80 غ")
    item.quantity = Decimal("77")
    item.save()
    call_command("seed_print_finance_defaults")
    rule.refresh_from_db()
    item.refresh_from_db()
    assert rule.price_per_sheet == Decimal("9.99") and item.quantity == Decimal("77")
    assert ExchangeRate.objects.count() == 1
    assert PrintPricingRule.objects.filter(currency="USD").count() == 12
    assert PrintPricingRule.objects.filter(currency="SYP").count() == 12


@pytest.mark.django_db
def test_quote_can_be_priced_in_usd_or_new_syp(customer):
    call_command("seed_print_finance_defaults")
    client = api(customer)
    syp = quote_request(client).json()["data"]  # default currency
    usd = quote_request(client, "USD").json()["data"]
    assert syp["currency"] == "SYP" and usd["currency"] == "USD"
    # 2 copies of 1 page + spiral: USD 0.03*2 + 0.60*2 = 1.26 ; SYP 4*2 + 72*2 = 152
    assert Decimal(str(usd["total_price"])) == Decimal("1.26")
    assert Decimal(str(syp["total_price"])) == Decimal("152")
    assert quote_request(client, "EUR").status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_missing_prices_for_the_chosen_currency_report_a_stable_code(customer):
    call_command("seed_print_finance_defaults")
    PrintPricingRule.objects.filter(currency="USD").update(is_active=False)
    response = quote_request(api(customer), "USD", binding="none")
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["code"] == "PRINT_PRICING_UNAVAILABLE"


# ---- inventory --------------------------------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_inventory_lifecycle_and_stock_never_goes_negative(admin, cashier):
    boss = api(admin)
    created = boss.post(
        "/api/v1/dashboard/finance/inventory-items/",
        {
            "name": "ورق A4",
            "category": "paper",
            "unit": "رزمة",
            "opening_quantity": "10",
            "reorder_level": "3",
            "unit_cost": "4",
            "currency": "USD",
        },
        format="json",
    )
    assert created.status_code == status.HTTP_201_CREATED, created.content
    item = InventoryItem.objects.get()
    assert item.quantity == Decimal("10") and StockMovement.objects.filter(item=item).count() == 1

    url = f"/api/v1/dashboard/printing/stock/{item.id}/movements/"
    assert boss.post(url, {"movement_type": "usage", "quantity": "4"}, format="json").status_code == 201
    item.refresh_from_db()
    assert item.quantity == Decimal("6")
    too_much = boss.post(url, {"movement_type": "usage", "quantity": "50"}, format="json")
    assert too_much.status_code == status.HTTP_400_BAD_REQUEST
    purchase = boss.post(
        url,
        {"movement_type": "purchase", "quantity": "5", "unit_cost": "4.50", "currency": "USD", "record_expense": True},
        format="json",
    )
    assert purchase.status_code == status.HTTP_201_CREATED
    item.refresh_from_db()
    assert item.quantity == Decimal("11") and item.unit_cost == Decimal("4.50")
    expense = Expense.objects.get()
    assert expense.amount == Decimal("22.50") and expense.currency == "USD"
    assert AuditLog.objects.filter(action=AuditAction.STOCK_MOVEMENT_RECORDED).count() == 2

    # print staff may use stock but cannot define items, expenses or rates
    cash = api(cashier)
    assert cash.post(url, {"movement_type": "usage", "quantity": "1"}, format="json").status_code == 201
    item_url = "/api/v1/dashboard/finance/inventory-items/"
    assert (
        cash.post(item_url, {"name": "x", "category": "paper"}, format="json").status_code == status.HTTP_403_FORBIDDEN
    )
    assert cash.get("/api/v1/dashboard/finance/summary/").status_code == status.HTTP_403_FORBIDDEN
    assert cash.get("/api/v1/dashboard/finance/expenses/").status_code == status.HTTP_403_FORBIDDEN
    assert cash.get("/api/v1/dashboard/printing/stock/").status_code == status.HTTP_200_OK
    assert cash.get(item_url).status_code == status.HTTP_403_FORBIDDEN


# ---- payments, print jobs and the summary ------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_payments_cannot_exceed_the_total_and_void_needs_a_manager(admin, cashier, customer):
    order = delivered_order(customer, "100", "SYP")
    cash = api(cashier)
    pay_url = "/api/v1/dashboard/printing/payments/"
    partial = cash.post(pay_url, {"order": order.id, "amount": "60", "method": "cash"}, format="json")
    assert partial.status_code == status.HTTP_201_CREATED, partial.content
    assert partial.json()["data"]["currency"] == "SYP"
    over = cash.post(pay_url, {"order": order.id, "amount": "50", "method": "cash"}, format="json")
    assert over.status_code == status.HTTP_400_BAD_REQUEST
    payment = OrderPayment.objects.get()
    void_url = f"/api/v1/dashboard/finance/payments/{payment.id}/void/"
    body = {"reason": "entered by mistake"}
    assert cash.post(void_url, body, format="json").status_code == status.HTTP_403_FORBIDDEN
    assert api(admin).post(void_url, body, format="json").status_code == status.HTTP_200_OK
    assert OrderPayment.objects.filter(is_deleted=False).count() == 0
    cancelled = PrintOrder.objects.create(
        user=customer, status=PrintOrderStatus.CANCELLED, total_price=Decimal("10"), currency="SYP"
    )
    assert cash.post(pay_url, {"order": cancelled.id, "amount": "5"}, format="json").status_code == 400


@pytest.mark.django_db
def test_print_jobs_list_totals_and_period_filter(admin, customer):
    delivered_order(customer, "100", "SYP", days_ago=0, pages=10)
    delivered_order(customer, "2.50", "USD", days_ago=1, pages=4)
    delivered_order(customer, "999", "SYP", days_ago=60)  # outside the requested window
    PrintOrder.objects.create(
        user=customer, status=PrintOrderStatus.SUBMITTED, total_price=Decimal("77"), currency="SYP"
    )
    today = timezone.localdate()
    url = f"/api/v1/dashboard/printing/jobs/?from={today - timedelta(days=5)}&to={today}"
    response = api(admin).get(url)
    assert response.status_code == status.HTTP_200_OK, response.content
    data = response.json()["data"]
    assert data["count"] == 2
    assert Decimal(str(data["totals_by_currency"]["SYP"]["total"])) == Decimal("100")
    assert Decimal(str(data["totals_by_currency"]["USD"]["total"])) == Decimal("2.5")
    first = data["results"][0]
    assert first["pages_count"] == 10 and first["is_paid"] is False and Decimal(str(first["due"])) == Decimal("100")
    assert api(admin).get("/api/v1/dashboard/printing/jobs/?from=2026-13-01").status_code == 400


@pytest.mark.django_db
def test_summary_combines_two_currencies_with_the_current_rate(admin, customer):
    today = timezone.localdate()
    delivered_order(customer, "1200", "SYP")  # = 10 USD at 120
    delivered_order(customer, "5", "USD")
    Expense.objects.create(
        spent_on=today, category="supplies", description="paper", amount=Decimal("3"), currency="USD", created_by=admin
    )
    Expense.objects.create(
        spent_on=today, category="rent", description="rent", amount=Decimal("240"), currency="SYP", created_by=admin
    )
    ExchangeRate.objects.create(syp_per_usd=Decimal("120"))
    boss = api(admin)

    usd = boss.get("/api/v1/dashboard/finance/summary/?currency=USD").json()["data"]
    assert Decimal(str(usd["totals"]["revenue"])) == Decimal("15")  # 10 + 5
    assert Decimal(str(usd["totals"]["expenses"])) == Decimal("5")  # 3 + 2
    assert Decimal(str(usd["totals"]["net"])) == Decimal("10")
    assert usd["orders_count"] == 2 and usd["revenue"]["SYP"] and usd["revenue"]["USD"]
    syp = boss.get("/api/v1/dashboard/finance/summary/?currency=SYP").json()["data"]
    assert Decimal(str(syp["totals"]["revenue"])) == Decimal("1800")
    assert boss.get("/api/v1/dashboard/finance/summary/?currency=EUR").status_code == status.HTTP_400_BAD_REQUEST

    ExchangeRate.objects.all().delete()
    unconverted = boss.get("/api/v1/dashboard/finance/summary/?currency=USD").json()["data"]
    assert unconverted["totals"]["revenue"] is None  # never guesses a rate
    assert unconverted["revenue"]["SYP"] is not None


@pytest.mark.django_db
def test_permission_override_can_be_removed_by_path_without_a_body(admin):
    target = make_user(UserRole.SUPPORT_STAFF, "support2@example.com", "+963944460009")
    UserPermissionOverride.objects.create(user=target, permission_code="printing.manage", effect="allow")
    response = api(admin).delete(f"/api/v1/dashboard/users/{target.id}/permission-overrides/printing.manage/")
    assert response.status_code == status.HTTP_200_OK, response.content
    assert not UserPermissionOverride.objects.filter(user=target, is_deleted=False).exists()
