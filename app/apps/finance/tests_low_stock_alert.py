from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.notifications.models import Notification

from .models import InventoryItem, StockMovementType
from .services import InventoryService, StockAlertService


def user(role, email, phone, **extra):
    return User.objects.create_user(
        full_name=email.split("@")[0], email=email, phone_number=phone, password="StrongPass123!", role=role, **extra
    )


@pytest.fixture
def staff(db):
    return {
        "cashier": user(UserRole.PRINT_STAFF, "alert-cashier@example.com", "+963944490001"),
        "admin": user(UserRole.ADMIN, "alert-admin@example.com", "+963944490002"),
        "student": user(UserRole.STUDENT, "alert-student@example.com", "+963944490003"),
    }


@pytest.fixture
def paper(db):
    return InventoryItem.objects.create(
        name="ورق A4", category="paper", unit="رزمة", quantity=Decimal("8"), reorder_level=Decimal("5")
    )


def use(item, amount, actor=None):
    return InventoryService.record_movement(item, StockMovementType.USAGE, Decimal(amount), actor=actor)


def alerts(recipient=None):
    queryset = Notification.objects.filter(data__kind__in=["low_stock", "out_of_stock"])
    return queryset.filter(user=recipient) if recipient else queryset


@pytest.mark.django_db
def test_crossing_the_reorder_level_alerts_the_printing_staff_once(staff, paper):
    use(paper, "2")  # 8 -> 6: still above the level of 5
    assert not alerts().exists()
    use(paper, "1.5")  # 6 -> 4.5: crossed
    first = alerts(staff["cashier"]).get()
    assert first.data["kind"] == "low_stock" and first.data["inventory_item_id"] == paper.pk
    assert "ورق A4" in first.title and "4.5" in first.body and "5" in first.body
    assert alerts(staff["admin"]).count() == 1
    assert not alerts(staff["student"]).exists()  # students never hear about the shop's shelf
    use(paper, "1")  # 4.5 -> 3.5: already low, no second alert
    assert alerts(staff["cashier"]).count() == 1


@pytest.mark.django_db
def test_running_out_alerts_again_with_a_stronger_message(staff, paper):
    use(paper, "4")  # 8 -> 4: low
    use(paper, "4")  # 4 -> 0: out
    kinds = list(alerts(staff["cashier"]).order_by("created_at").values_list("data__kind", flat=True))
    assert kinds == ["low_stock", "out_of_stock"]
    assert "نفد" in alerts(staff["cashier"]).filter(data__kind="out_of_stock").get().title


@pytest.mark.django_db
def test_buying_stock_back_over_the_level_re_arms_the_alert(staff, paper):
    use(paper, "4")  # low
    InventoryService.record_movement(paper, StockMovementType.PURCHASE, Decimal("10"))  # 14: back above
    assert alerts(staff["cashier"]).count() == 1  # a purchase never alerts
    use(paper, "10")  # 14 -> 4: crosses again
    assert alerts(staff["cashier"]).count() == 2


@pytest.mark.django_db
def test_an_item_with_no_reorder_level_or_not_in_use_never_alerts(staff):
    silent = InventoryItem.objects.create(name="بلا حد", unit="قطعة", quantity=Decimal("3"), reorder_level=Decimal("0"))
    use(silent, "2")
    assert not alerts().exists()  # level 0 means the alert is off
    use(silent, "1")  # reaching zero is still worth knowing
    assert alerts(staff["cashier"]).get().data["kind"] == "out_of_stock"
    inactive = InventoryItem.objects.create(
        name="قديم", unit="قطعة", quantity=Decimal("6"), reorder_level=Decimal("5"), is_active=False
    )
    use(inactive, "3")
    assert alerts(staff["cashier"]).count() == 1  # nothing new for the retired item


@pytest.mark.django_db
def test_a_staff_member_without_the_printing_capability_is_not_told(staff, paper):
    from apps.accounts.choices import PermissionEffect
    from apps.accounts.models import UserPermissionOverride

    UserPermissionOverride.objects.create(
        user=staff["cashier"], permission_code="printing.manage", effect=PermissionEffect.DENY
    )
    use(paper, "4")
    assert not alerts(staff["cashier"]).exists() and alerts(staff["admin"]).count() == 1


@pytest.mark.django_db
def test_the_alert_decision_is_a_pure_function_of_before_and_after(paper):
    kind = StockAlertService.kind
    assert kind(paper, Decimal("6"), Decimal("5")) == "low_stock"  # exactly at the level counts
    assert kind(paper, Decimal("6"), Decimal("5.01")) is None
    assert kind(paper, Decimal("5"), Decimal("4")) is None  # was already low
    assert kind(paper, Decimal("1"), Decimal("0")) == "out_of_stock"
    assert kind(paper, Decimal("0"), Decimal("0")) is None


@pytest.mark.django_db
def test_the_automatic_deduction_on_delivery_raises_the_alert_too(staff, paper):
    from apps.printing.models import PrintOrder, PrintOrderItem, PrintOrderStatus
    from apps.printing.services import PrintStatusService

    paper.consumption_key, paper.consumption_per_use, paper.quantity = "paper_a4", Decimal("0.5"), Decimal("5.5")
    paper.save()
    order = PrintOrder.objects.create(
        user=staff["student"], status=PrintOrderStatus.READY, total_price=Decimal("10"), currency="SYP"
    )
    PrintOrderItem.objects.create(
        order=order, pages_count=2, copies=1, sheets_count=2, paper_size="A4", price=Decimal("10")
    )
    PrintStatusService.change_status(order, PrintOrderStatus.DELIVERED, staff["cashier"])  # takes 1.0: 5.5 -> 4.5
    assert alerts(staff["cashier"]).get().data["kind"] == "low_stock"


@pytest.mark.django_db
def test_the_stock_list_can_be_limited_to_what_is_low(staff, paper):
    InventoryItem.objects.create(name="وفير", unit="قطعة", quantity=Decimal("50"), reorder_level=Decimal("5"))
    use(paper, "4")
    client = APIClient()
    client.force_authenticate(user=staff["cashier"])
    rows = client.get("/api/v1/dashboard/printing/stock/?low=true&active=true").json()["data"]
    rows = rows["results"] if isinstance(rows, dict) else rows
    assert [row["name"] for row in rows] == ["ورق A4"] and rows[0]["is_low"] is True
