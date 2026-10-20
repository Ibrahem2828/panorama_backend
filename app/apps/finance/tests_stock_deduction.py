from decimal import Decimal

import pytest
from django.core.management import call_command
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.audit.models import AuditAction, AuditLog
from apps.printing.models import PrintOrder, PrintOrderItem, PrintOrderStatus
from apps.printing.services import PrintStatusService

from .models import InventoryItem, StockMovement
from .services import StockConsumptionService

COUNTER = "/api/v1/dashboard/printing/counter-orders/"


def make_cashier():
    return User.objects.create_user(
        full_name="cashier",
        email="stock-cashier@example.com",
        phone_number="+963944480001",
        password="StrongPass123!",
        role=UserRole.PRINT_STAFF,
    )


def api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def cashier(db):
    return make_cashier()


@pytest.fixture
def shelf(db):
    call_command("seed_print_finance_defaults")
    return {
        key: InventoryItem.objects.get(consumption_key=key)
        for key in ("paper_a4", "paper_a3", "binding_spiral", "binding_staple")
    }


def line(**over):
    base = {
        "pages_count": 10,
        "copies": 2,
        "color_mode": "black_white",
        "paper_size": "A4",
        "sides": "one_sided",
        "binding": "spiral",
    }
    return {**base, **over}


def counter_order(client, lines, status="delivered"):
    response = client.post(COUNTER, {"items": lines, "status": status}, format="json")
    assert response.status_code == 201, response.json()
    return response.json()["data"]


def reload(item):
    item.refresh_from_db()
    return item


# ---- the seed maps the shelf -----------------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_maps_paper_and_binding_to_what_orders_use(shelf):
    assert shelf["paper_a4"].consumption_per_use == Decimal("0.002")
    assert shelf["binding_spiral"].consumption_per_use == Decimal("0.01")
    assert (
        not InventoryItem.objects.filter(name__startswith="حبر").exclude(consumption_key="").exists()
    )  # ink stays manual


@pytest.mark.django_db
def test_seed_never_overwrites_a_rule_the_owner_edited(shelf):
    paper = shelf["paper_a4"]
    paper.consumption_per_use = Decimal("0.004")
    paper.save()
    shelf["paper_a3"].consumption_key = ""
    shelf["paper_a3"].consumption_per_use = Decimal("0")
    shelf["paper_a3"].save()
    call_command("seed_print_finance_defaults")
    assert reload(paper).consumption_per_use == Decimal("0.004")
    assert reload(shelf["paper_a3"]).consumption_key == "paper_a3"  # an empty rule is filled again


@pytest.mark.django_db
def test_only_one_item_may_carry_a_consumption_key(shelf):
    with pytest.raises(IntegrityError), transaction.atomic():
        InventoryItem.objects.create(
            name="ورق ثانٍ", category="paper", consumption_key="paper_a4", consumption_per_use=Decimal("0.002")
        )


# ---- delivering deducts -----------------------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_delivering_a_counter_order_takes_paper_and_binding_from_the_shelf(shelf, cashier):
    data = counter_order(api(cashier), [line()])  # 20 sheets x 0.002 = 0.04 reams; 2 spiral copies x 0.01 = 0.02 boxes
    assert reload(shelf["paper_a4"]).quantity == Decimal("19.96")
    assert reload(shelf["binding_spiral"]).quantity == Decimal("2.98")
    order = PrintOrder.objects.get(pk=data["id"])
    assert order.stock_deducted_at is not None
    moves = StockMovement.objects.filter(order=order)
    assert {move.item.consumption_key: move.quantity for move in moves} == {
        "paper_a4": Decimal("-0.04"),
        "binding_spiral": Decimal("-0.02"),
    }
    assert all(move.movement_type == "usage" and move.created_by == cashier for move in moves)
    assert (
        AuditLog.objects.filter(
            action=AuditAction.STOCK_MOVEMENT_RECORDED, new_value__print_order=order.pk, new_value__automatic=True
        ).count()
        == 2
    )
    assert {row["item"]: row["quantity"] for row in data["stock_usage"]} == {
        shelf["paper_a4"].name: "0.04",
        shelf["binding_spiral"].name: "0.02",
    }


@pytest.mark.django_db
def test_an_order_only_deducts_when_it_is_delivered_and_only_once(shelf, cashier):
    client = api(cashier)
    data = counter_order(client, [line()], status="printing")
    order = PrintOrder.objects.get(pk=data["id"])
    assert reload(shelf["paper_a4"]).quantity == Decimal("20.00") and order.stock_deducted_at is None
    for new_status in ("ready", "delivered"):
        assert (
            client.patch(
                f"/api/v1/dashboard/printing/orders/{order.pk}/status/", {"status": new_status}, format="json"
            ).status_code
            == 200
        )
    assert reload(shelf["paper_a4"]).quantity == Decimal("19.96")
    assert StockConsumptionService.deduct_for_order(order) == []  # asked again: nothing happens
    assert reload(shelf["paper_a4"]).quantity == Decimal("19.96")
    assert StockMovement.objects.filter(order=order).count() == 2


@pytest.mark.django_db
def test_an_app_order_deducts_by_paper_size_and_ignores_unmapped_bindings(shelf, cashier):
    order = PrintOrder.objects.create(
        user=cashier, status=PrintOrderStatus.READY, total_price=Decimal("100"), currency="SYP"
    )
    PrintOrderItem.objects.create(
        order=order, pages_count=3, copies=2, sheets_count=6, paper_size="A3", binding="thermal", price=Decimal("100")
    )
    PrintStatusService.change_status(order, PrintOrderStatus.DELIVERED, cashier)
    assert reload(shelf["paper_a3"]).quantity == Decimal(
        "4.99"
    )  # 6 x 0.002 = 0.012 -> a hundredth is taken, 0.002 waits
    assert reload(shelf["paper_a3"]).pending_consumption == Decimal("0.002000")
    assert StockMovement.objects.filter(order=order).count() == 1  # thermal has no stock item: nothing for it


# ---- small amounts add up ---------------------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_single_sheets_add_up_instead_of_vanishing(shelf, cashier):
    client = api(cashier)
    for _ in range(4):
        counter_order(client, [line(pages_count=1, copies=1, binding="none")])
    paper = reload(shelf["paper_a4"])
    assert paper.quantity == Decimal("20.00") and paper.pending_consumption == Decimal("0.008000")  # 4 sheets so far
    counter_order(client, [line(pages_count=1, copies=1, binding="none")])
    paper = reload(shelf["paper_a4"])
    assert paper.quantity == Decimal("19.99") and paper.pending_consumption == Decimal(
        "0.000000"
    )  # the fifth sheet makes 0.01


# ---- running short and not being set up -------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_running_short_never_blocks_the_delivery_and_the_missing_amount_stays_owed(shelf, cashier):
    paper = shelf["paper_a4"]
    paper.quantity = Decimal("0.02")
    paper.save()
    data = counter_order(api(cashier), [line(binding="none")])  # needs 0.04, only 0.02 on the shelf
    order = PrintOrder.objects.get(pk=data["id"])
    assert order.status == PrintOrderStatus.DELIVERED
    paper = reload(paper)
    assert paper.quantity == Decimal("0.00") and paper.pending_consumption == Decimal("0.020000")
    move = StockMovement.objects.get(order=order)
    assert move.quantity == Decimal("-0.02") and "النقص" in move.note


@pytest.mark.django_db
def test_items_without_a_rule_or_inactive_are_skipped_but_the_order_is_still_marked(shelf, cashier):
    paper = shelf["paper_a4"]
    paper.is_active = False
    paper.save()
    data = counter_order(api(cashier), [line(binding="none")])
    order = PrintOrder.objects.get(pk=data["id"])
    assert reload(paper).quantity == Decimal("20.00")
    assert order.stock_deducted_at is not None and not StockMovement.objects.filter(order=order).exists()


@pytest.mark.django_db
def test_a_shop_that_maps_nothing_deducts_nothing(cashier):
    call_command("seed_print_finance_defaults")
    InventoryItem.objects.update(consumption_key="", consumption_per_use=Decimal("0"))
    counter_order(api(cashier), [line()])
    assert not StockMovement.objects.filter(order__isnull=False).exists()


# ---- visibility --------------------------------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_movement_history_and_the_item_expose_the_order_and_the_rule(shelf, cashier):
    client = api(cashier)
    data = counter_order(client, [line()])
    history = client.get(f"/api/v1/dashboard/printing/stock/{shelf['paper_a4'].pk}/movement-history/").json()["data"]
    assert history[0]["order"] == data["id"] and history[0]["quantity"] == "-0.04"
    item = client.get(f"/api/v1/dashboard/printing/stock/{shelf['paper_a4'].pk}/").json()["data"]
    assert item["consumption_key"] == "paper_a4" and Decimal(item["consumption_per_use"]) == Decimal("0.002")
    assert "pending_consumption" in item


@pytest.mark.django_db
def test_the_owner_can_change_the_rule_from_the_inventory_item(shelf):
    admin = User.objects.create_user(
        full_name="owner",
        email="stock-owner@example.com",
        phone_number="+963944480002",
        password="StrongPass123!",
        role=UserRole.ADMIN,
    )
    client = api(admin)
    url = f"/api/v1/dashboard/finance/inventory-items/{shelf['paper_a4'].pk}/"
    response = client.patch(url, {"consumption_per_use": "0.0025"}, format="json")
    assert response.status_code == 200, response.json()
    assert reload(shelf["paper_a4"]).consumption_per_use == Decimal("0.002500")
    assert client.patch(url, {"consumption_key": "paper_a0"}, format="json").status_code == 400
    assert (
        client.patch(url, {"consumption_key": "paper_a3"}, format="json").status_code == 400
    )  # already used by the A3 ream
