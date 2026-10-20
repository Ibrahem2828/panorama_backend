from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.audit.models import AuditAction, AuditLog
from apps.common.tests_phase3 import PNG_BYTES
from apps.finance.models import OrderPayment

from .models import PrintOrder, PrintOrderSource, PrintOrderStatus
from .services import round_price, round_syp

COUNTER = "/api/v1/dashboard/printing/counter-orders/"
QUOTE = "/api/v1/dashboard/printing/counter-orders/quote/"


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
    return make_user(UserRole.PRINT_STAFF, "counter-cashier@example.com", "+963944470001")


@pytest.fixture
def customer(db):
    return make_user(UserRole.NORMAL_USER, "counter-customer@example.com", "+963944470002")


@pytest.fixture
def prices(db):
    call_command("seed_print_finance_defaults")


def line(**over):
    # 10 pages x 2 copies, black and white, A4, one side, spiral: 20 sheets x 4 + 2 x 72 = 224 -> quoted 230 (a last digit of 3 or more rounds up)
    base = {
        "pages_count": 10,
        "copies": 2,
        "color_mode": "black_white",
        "paper_size": "A4",
        "sides": "one_sided",
        "binding": "spiral",
    }
    return {**base, **over}


# ---- the rounding rule ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        ("0", "0.00"),
        ("1", "10.00"),  # a positive price never becomes zero
        ("2", "10.00"),
        ("3", "10.00"),
        ("9", "10.00"),
        ("10", "10.00"),
        ("11", "10.00"),
        ("12", "10.00"),
        ("13", "20.00"),
        ("151", "150.00"),
        ("152", "150.00"),
        ("153", "160.00"),
        ("154", "160.00"),
        ("155", "160.00"),
        ("156", "160.00"),
        ("158", "160.00"),
        ("159", "160.00"),
        ("160", "160.00"),
        ("161", "160.00"),
        ("162", "160.00"),
        ("163", "170.00"),
        ("0.4", "0.00"),
        ("0.5", "10.00"),  # half a pound rounds to a pound first, then to ten
        ("152.4", "150.00"),
        ("152.6", "160.00"),
        ("1234.5", "1240.00"),
    ],
)
def test_syrian_pound_prices_round_to_tens(amount, expected):
    assert round_syp(Decimal(amount)) == Decimal(expected)


def test_only_the_new_pound_is_rounded_to_tens():
    assert round_price(Decimal("156"), "SYP") == Decimal("160.00")
    assert round_price(Decimal("1.2345"), "USD") == Decimal("1.23")
    assert round_price(Decimal("1.2350"), "USD") == Decimal("1.24")


@pytest.mark.django_db
def test_app_quote_and_order_are_rounded_and_say_by_how_much(prices, customer):
    client = api(customer)

    def request(path):
        upload = SimpleUploadedFile("a.png", PNG_BYTES, content_type="image/png")
        return client.post(
            path,
            {
                "items[0]uploaded_file": upload,
                "items[0]copies": 2,
                "items[0]color_mode": "black_white",
                "items[0]paper_size": "A4",
                "items[0]sides": "one_sided",
                "items[0]binding": "spiral",
            },
            format="multipart",
        )

    quote = request("/api/v1/printing/quote/").json()["data"]
    # 2 x 1 page x 4 + 2 x 72 = 152 exactly, shown to the customer as 150
    assert Decimal(str(quote["total_price"])) == Decimal("150")
    assert Decimal(str(quote["exact_total"])) == Decimal("152")
    assert Decimal(str(quote["rounding_adjustment"])) == Decimal("-2")
    order = request("/api/v1/printing/orders/").json()["data"]
    assert Decimal(order["total_price"]) == Decimal("150")
    assert Decimal(order["rounding_adjustment"]) == Decimal("-2")
    assert PrintOrder.objects.get(pk=order["id"]).total_price == Decimal("150.00")


# ---- counter quote ----------------------------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_counter_quote_prices_lines_and_rounds_the_total(prices, cashier):
    data = api(cashier).post(QUOTE, {"items": [line()]}, format="json").json()["data"]
    assert data["complete"] is True
    assert Decimal(str(data["exact_total"])) == Decimal("224")
    assert Decimal(str(data["suggested_total"])) == Decimal("230")
    assert Decimal(str(data["rounding_adjustment"])) == Decimal("6")
    item = data["items"][0]
    assert item["sheets_count"] == 20 and Decimal(str(item["subtotal"])) == Decimal("224")


@pytest.mark.django_db
def test_double_sided_lines_use_half_the_sheets(prices, cashier):
    data = (
        api(cashier)
        .post(QUOTE, {"items": [line(sides="double_sided", binding="none", pages_count=11)]}, format="json")
        .json()["data"]
    )
    assert data["items"][0]["sheets_count"] == 12  # ceil(11 / 2) x 2 copies


@pytest.mark.django_db
def test_a_line_without_a_price_is_reported_not_guessed(prices, cashier):
    data = (
        api(cashier)
        .post(QUOTE, {"items": [line(), line(binding="beads", binding_note="")]}, format="json")
        .json()["data"]
    )
    assert data["complete"] is False
    assert data["suggested_total"] is None and data["rounding_adjustment"] is None
    assert data["items"][1]["unavailable"] == "binding" and data["items"][1]["subtotal"] is None


@pytest.mark.django_db
def test_counter_quote_validates_its_input(prices, cashier):
    client = api(cashier)
    assert client.post(QUOTE, {"items": []}, format="json").status_code == status.HTTP_400_BAD_REQUEST
    assert client.post(QUOTE, {"items": [line(copies=0)]}, format="json").status_code == status.HTTP_400_BAD_REQUEST
    assert (
        client.post(QUOTE, {"items": [line(pages_count=0)]}, format="json").status_code == status.HTTP_400_BAD_REQUEST
    )
    assert (
        client.post(QUOTE, {"items": [line(binding="glue")]}, format="json").status_code == status.HTTP_400_BAD_REQUEST
    )


# ---- creating a counter order ----------------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_counter_order_defaults_to_delivered_and_paid_at_the_rounded_price(prices, cashier):
    response = api(cashier).post(
        COUNTER,
        {"customer_name": "أحمد", "customer_phone": "0944000000", "items": [line(title="محاضرة")]},
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()["data"]
    order = PrintOrder.objects.get(pk=data["id"])
    assert order.user is None and order.source == PrintOrderSource.COUNTER and order.created_by == cashier
    assert order.status == PrintOrderStatus.DELIVERED and order.completed_at is not None
    assert order.total_price == Decimal("230.00")
    assert data["user_name"] == "أحمد" and data["source"] == "counter"
    assert Decimal(data["paid_amount"]) == Decimal("230.00") and Decimal(data["due_amount"]) == Decimal("0.00")
    assert Decimal(data["rounding_adjustment"]) == Decimal("6.00")
    payment = OrderPayment.objects.get(order=order)
    assert payment.amount == Decimal("230.00") and payment.received_by == cashier
    item = order.items.get()
    assert item.sheets_count == 20 and item.price == Decimal("224.00") and item.binding == "spiral"
    assert order.status_history.count() == 1
    assert AuditLog.objects.filter(action=AuditAction.PRINT_ORDER_CREATED, target_id=str(order.pk)).exists()


@pytest.mark.django_db
def test_the_cashier_can_charge_a_different_price_and_take_part_payment(prices, cashier):
    body = {
        "items": [line()],
        "total_price": "200",
        "status": "printing",
        "paid_amount": "100",
        "payment_method": "cash",
    }
    data = api(cashier).post(COUNTER, body, format="json").json()["data"]
    order = PrintOrder.objects.get(pk=data["id"])
    assert order.total_price == Decimal("200.00") and order.status == PrintOrderStatus.PRINTING
    assert order.completed_at is None
    assert Decimal(data["paid_amount"]) == Decimal("100.00") and Decimal(data["due_amount"]) == Decimal("100.00")
    assert order.pricing_snapshot["price_overridden"] is True
    assert order.pricing_snapshot["suggested_total"] == "230.00"


@pytest.mark.django_db
def test_an_unpriced_line_needs_a_typed_price_and_other_bindings_keep_their_note(prices, cashier):
    client = api(cashier)
    unpriced = {"items": [line(binding="other", binding_note="دباسة معدنية")]}
    assert client.post(COUNTER, unpriced, format="json").status_code == status.HTTP_400_BAD_REQUEST
    response = client.post(COUNTER, {**unpriced, "total_price": "300"}, format="json")
    assert response.status_code == status.HTTP_201_CREATED
    item = PrintOrder.objects.get(pk=response.json()["data"]["id"]).items.get()
    assert item.binding == "other" and item.binding_note == "دباسة معدنية" and item.price == Decimal("0.00")


@pytest.mark.django_db
def test_bad_counter_orders_are_rejected_and_leave_nothing_behind(prices, cashier):
    client = api(cashier)
    assert client.post(COUNTER, {"items": [line()], "paid_amount": "500"}, format="json").status_code == 400
    assert client.post(COUNTER, {"items": [line()], "status": "submitted"}, format="json").status_code == 400
    assert client.post(COUNTER, {"items": [line()], "payment_method": "barter"}, format="json").status_code == 400
    assert client.post(COUNTER, {"items": [line()], "total_price": "-5"}, format="json").status_code == 400
    assert PrintOrder.objects.count() == 0 and OrderPayment.objects.count() == 0


@pytest.mark.django_db
def test_a_retried_submit_with_the_same_key_does_not_charge_twice(prices, cashier):
    client = api(cashier)
    body = {"items": [line()]}
    first = client.post(COUNTER, body, format="json", HTTP_IDEMPOTENCY_KEY="counter-key-0001")
    second = client.post(COUNTER, body, format="json", HTTP_IDEMPOTENCY_KEY="counter-key-0001")
    assert first.status_code == second.status_code == status.HTTP_201_CREATED
    assert first.json()["data"]["id"] == second.json()["data"]["id"]
    assert PrintOrder.objects.count() == 1 and OrderPayment.objects.count() == 1


@pytest.mark.django_db
def test_only_printing_staff_can_use_the_counter_endpoints(prices, customer, cashier):
    assert APIClient().post(COUNTER, {"items": [line()]}, format="json").status_code == status.HTTP_401_UNAUTHORIZED
    assert api(customer).post(COUNTER, {"items": [line()]}, format="json").status_code == status.HTTP_403_FORBIDDEN
    assert api(customer).post(QUOTE, {"items": [line()]}, format="json").status_code == status.HTTP_403_FORBIDDEN
    assert api(cashier).post(QUOTE, {"items": [line()]}, format="json").status_code == status.HTTP_200_OK


# ---- the rest of the order life cycle works without an account --------------------------------------------------------------------


@pytest.mark.django_db
def test_a_counter_order_moves_through_the_normal_statuses_without_notifying_anyone(prices, cashier):
    client = api(cashier)
    order_id = client.post(COUNTER, {"items": [line()], "status": "printing"}, format="json").json()["data"]["id"]
    for new_status in ("ready", "delivered"):
        response = client.patch(
            f"/api/v1/dashboard/printing/orders/{order_id}/status/", {"status": new_status}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
    assert PrintOrder.objects.get(pk=order_id).completed_at is not None


@pytest.mark.django_db
def test_the_order_list_shows_source_customer_and_amounts_and_can_be_filtered(prices, cashier, customer):
    client = api(cashier)
    client.post(
        COUNTER, {"customer_name": "سلمى", "items": [line()], "status": "ready", "paid_amount": "20"}, format="json"
    )
    PrintOrder.objects.create(user=customer, total_price=Decimal("50"), currency="SYP")
    everything = client.get("/api/v1/dashboard/printing/orders/").json()["data"]
    rows = everything["results"] if isinstance(everything, dict) else everything
    assert {row["source"] for row in rows} == {"app", "counter"}
    counter_rows = client.get("/api/v1/dashboard/printing/orders/?source=counter").json()["data"]
    counter_rows = counter_rows["results"] if isinstance(counter_rows, dict) else counter_rows
    assert [row["user_name"] for row in counter_rows] == ["سلمى"]
    assert Decimal(counter_rows[0]["paid_amount"]) == Decimal("20.00")
    assert Decimal(counter_rows[0]["due_amount"]) == Decimal("210.00")
    found = client.get("/api/v1/dashboard/printing/orders/?search=سلمى").json()["data"]
    found = found["results"] if isinstance(found, dict) else found
    assert len(found) == 1
