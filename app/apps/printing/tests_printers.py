"""Printer registry, the agent heartbeat, job cancel, counter-order duplex/page range and the order date filters."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.audit.models import AuditAction, AuditLog

from .jobs import PrintJobService
from .models import Printer, PrintJob, PrintJobStatus, PrintOrder, PrintOrderStatus
from .services import PrintOrderService, PrintStatusService
from .tests_print_jobs import make_pdf

PRINTERS = "/api/v1/dashboard/printing/printers/"
HEARTBEAT = "/api/v1/printing/agent/heartbeat/"
COUNTER = "/api/v1/dashboard/printing/counter-orders/"
QUOTE = "/api/v1/dashboard/printing/counter-orders/quote/"
ORDERS = "/api/v1/dashboard/printing/orders/"


def make_user(role, email, phone):
    return User.objects.create_user(
        full_name=email.split("@")[0], email=email, phone_number=phone, password="StrongPass123!", role=role
    )


def api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def rows(response):
    data = response.json()["data"]
    return data["results"] if isinstance(data, dict) else data


@pytest.fixture
def prices(db):
    call_command("seed_print_finance_defaults")


@pytest.fixture
def staff(db):
    return make_user(UserRole.PRINT_STAFF, "printers-staff@example.com", "+963944480001")


@pytest.fixture
def customer(db):
    return make_user(UserRole.NORMAL_USER, "printers-customer@example.com", "+963944480002")


@pytest.fixture
def job(prices, customer, staff):
    order = PrintOrderService.create_order(
        customer,
        [
            {
                "uploaded_file": SimpleUploadedFile("lecture.pdf", make_pdf(4), content_type="application/pdf"),
                "copies": 1,
                "color_mode": "black_white",
                "paper_size": "A4",
                "sides": "one_sided",
                "binding": "none",
            }
        ],
    )
    for status in (PrintOrderStatus.UNDER_REVIEW, PrintOrderStatus.ACCEPTED):
        PrintStatusService.change_status(order, status, staff)
    order.refresh_from_db()
    return PrintJobService.create_for_order(order, staff)[0]


def job_url(job, action):
    return f"/api/v1/dashboard/printing/print-jobs/{job.pk}/{action}/"


# ---- registry ---------------------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_printer_crud_is_audited_and_names_are_unique(staff):
    client = api(staff)
    created = client.post(
        PRINTERS, {"name": "HP LaserJet", "station": "Main shop", "notes": "left tray"}, format="json"
    )
    assert created.status_code == 201, created.json()
    body = created.json()["data"]
    assert body["online"] is False and body["jobs"] == {"queued": 0, "printing": 0, "failed_24h": 0}
    assert body["is_active"] is True and body["is_default"] is False

    assert client.post(PRINTERS, {"name": "HP LaserJet"}, format="json").status_code == 400

    patched = client.patch(f"{PRINTERS}{body['id']}/", {"station": "Branch"}, format="json")
    assert patched.status_code == 200 and patched.json()["data"]["station"] == "Branch"
    assert AuditLog.objects.filter(action=AuditAction.PRINTER_CHANGED).count() == 2

    deleted = client.delete(f"{PRINTERS}{body['id']}/")
    assert deleted.status_code == 200
    assert Printer.objects.get(pk=body["id"]).is_deleted is True
    assert AuditLog.objects.filter(action=AuditAction.PRINTER_CHANGED).count() == 3
    # the name is free again once the printer is gone
    assert client.post(PRINTERS, {"name": "HP LaserJet"}, format="json").status_code == 201


@pytest.mark.django_db
def test_only_one_default_printer(staff):
    client = api(staff)
    first = client.post(PRINTERS, {"name": "A", "is_default": True}, format="json").json()["data"]
    second = client.post(PRINTERS, {"name": "B", "is_default": True}, format="json").json()["data"]
    assert Printer.objects.get(pk=first["id"]).is_default is False
    assert Printer.objects.get(pk=second["id"]).is_default is True
    client.patch(f"{PRINTERS}{first['id']}/", {"is_default": True}, format="json")
    assert list(Printer.objects.filter(is_default=True).values_list("name", flat=True)) == ["A"]
    # an inactive printer cannot become the default, and deactivating the default drops the flag
    assert (
        client.patch(f"{PRINTERS}{second['id']}/", {"is_active": False, "is_default": True}, format="json").status_code
        == 400
    )
    client.patch(f"{PRINTERS}{first['id']}/", {"is_active": False}, format="json")
    assert Printer.objects.get(pk=first["id"]).is_default is False


@pytest.mark.django_db
def test_list_search_filter_online_and_job_counts(staff, job):
    now = timezone.now()
    busy = Printer.objects.create(name="Epson", station="Shop", last_seen_at=now - timedelta(seconds=30))
    Printer.objects.create(name="Old Canon", station="Store", is_active=False, last_seen_at=now - timedelta(minutes=10))
    job.printer = busy
    job.save()
    PrintJob.objects.create(
        order=job.order, item=job.item, printer=busy, status=PrintJobStatus.PRINTING, started_at=now
    )
    PrintJob.objects.create(
        order=job.order, item=job.item, printer=busy, status=PrintJobStatus.FAILED, finished_at=now - timedelta(hours=1)
    )
    PrintJob.objects.create(
        order=job.order, item=job.item, printer=busy, status=PrintJobStatus.FAILED, finished_at=now - timedelta(days=3)
    )
    client = api(staff)
    data = {row["name"]: row for row in rows(client.get(PRINTERS))}
    assert data["Epson"]["online"] is True
    assert data["Epson"]["jobs"] == {"queued": 1, "printing": 1, "failed_24h": 1}
    assert data["Old Canon"]["online"] is False
    assert [r["name"] for r in rows(client.get(PRINTERS + "?is_active=false"))] == ["Old Canon"]
    assert [r["name"] for r in rows(client.get(PRINTERS + "?search=store"))] == ["Old Canon"]


@pytest.mark.django_db
def test_deleting_a_printer_with_jobs_only_deactivates_it(staff, job):
    printer = Printer.objects.create(name="Used", is_default=True)
    job.printer = printer
    job.save()
    response = api(staff).delete(f"{PRINTERS}{printer.pk}/")
    assert response.status_code == 200
    printer.refresh_from_db()
    assert printer.is_deleted is False and printer.is_active is False and printer.is_default is False
    assert response.json()["data"]["jobs"]["queued"] == 1


@pytest.mark.django_db
def test_printer_endpoints_need_the_printing_capability(customer):
    client = api(customer)
    assert client.get(PRINTERS).status_code == 403
    assert client.post(PRINTERS, {"name": "X"}, format="json").status_code == 403
    assert client.post(HEARTBEAT, {"printer_name": "X", "status": "idle"}, format="json").status_code == 403
    assert APIClient().get(PRINTERS).status_code in (401, 403)
    assert APIClient().post(HEARTBEAT, {"printer_name": "X", "status": "idle"}, format="json").status_code in (401, 403)
    assert not Printer.objects.exists()


# ---- heartbeat --------------------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_heartbeat_registers_and_refreshes_a_printer(staff):
    client = api(staff)
    first = client.post(HEARTBEAT, {"printer_name": " Kitchen Printer ", "status": "idle"}, format="json")
    assert first.status_code == 200, first.json()
    body = first.json()["data"]
    assert body["name"] == "Kitchen Printer" and body["online"] is True and body["last_status"] == "idle"
    assert body["is_active"] is True

    Printer.objects.filter(pk=body["id"]).update(last_seen_at=timezone.now() - timedelta(minutes=5))
    again = client.post(
        HEARTBEAT, {"printer_name": "Kitchen Printer", "status": "error", "detail": "paper jam"}, format="json"
    )
    assert again.json()["data"]["id"] == body["id"]
    assert again.json()["data"]["online"] is True and again.json()["data"]["last_detail"] == "paper jam"
    assert Printer.objects.count() == 1


@pytest.mark.django_db
def test_heartbeat_validates_the_body(staff):
    client = api(staff)
    assert client.post(HEARTBEAT, {"printer_name": "P", "status": "sleeping"}, format="json").status_code == 400
    assert client.post(HEARTBEAT, {"printer_name": "", "status": "idle"}, format="json").status_code == 400
    assert client.post(HEARTBEAT, {"status": "idle"}, format="json").status_code == 400


@pytest.mark.django_db
def test_job_progress_links_the_registered_printer(job, staff):
    printer = Printer.objects.create(name="HP")
    client = api(staff)
    response = client.patch(job_url(job, "status"), {"status": "printing", "printer_name": "HP"}, format="json")
    assert response.status_code == 200 and response.json()["data"]["printer"] == printer.pk
    job.refresh_from_db()
    assert job.printer == printer
    other = client.patch(job_url(job, "status"), {"status": "done", "printer_name": "Unknown"}, format="json")
    assert other.status_code == 200 and other.json()["data"]["printer_name"] == "Unknown"


# ---- cancel -----------------------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_cancel_a_queued_job(job, staff):
    order_status = job.order.status
    response = api(staff).post(job_url(job, "cancel"), {"reason": "wrong file"}, format="json")
    assert response.status_code == 200, response.json()
    assert response.json()["data"]["status"] == "cancelled"
    job.refresh_from_db()
    assert job.status == PrintJobStatus.CANCELLED and job.finished_at and job.error == "wrong file"
    assert PrintOrder.objects.get(pk=job.order_id).status == order_status  # the order is untouched
    log = AuditLog.objects.get(action=AuditAction.PRINT_JOB_CANCELLED)
    assert log.new_value["reason"] == "wrong file"
    # cancelling twice is harmless; the agent can no longer take or finish it
    assert api(staff).post(job_url(job, "cancel"), {}, format="json").status_code == 200
    assert api(staff).patch(job_url(job, "status"), {"status": "printing"}, format="json").status_code == 409
    assert api(staff).post(job_url(job, "retry"), {}, format="json").status_code == 409
    assert api(staff).patch(job_url(job, "status"), {"status": "cancelled"}, format="json").status_code == 400
    queued = rows(api(staff).get("/api/v1/dashboard/printing/print-jobs/?status=queued"))
    assert queued == []


@pytest.mark.django_db
def test_cancel_a_printing_job_then_agent_completion_conflicts(job, staff):
    client = api(staff)
    client.patch(job_url(job, "status"), {"status": "printing"}, format="json")
    assert client.post(job_url(job, "cancel"), {}, format="json").status_code == 200
    assert client.patch(job_url(job, "status"), {"status": "done"}, format="json").status_code == 409


@pytest.mark.django_db
def test_cancel_done_or_failed_job_is_409(job, staff):
    client = api(staff)
    client.patch(job_url(job, "status"), {"status": "done"}, format="json")
    assert client.post(job_url(job, "cancel"), {}, format="json").status_code == 409
    failed = PrintJob.objects.create(order=job.order, item=job.item, status=PrintJobStatus.FAILED)
    assert client.post(job_url(failed, "cancel"), {}, format="json").status_code == 409
    assert client.post("/api/v1/dashboard/printing/print-jobs/999999/cancel/", {}, format="json").status_code == 404


@pytest.mark.django_db
def test_cancel_needs_the_printing_capability(job, customer):
    assert api(customer).post(job_url(job, "cancel"), {}, format="json").status_code == 403
    job.refresh_from_db()
    assert job.status == PrintJobStatus.QUEUED


# ---- counter orders: duplex flip and page range ------------------------------------------------------------------------


def line(**over):
    base = {
        "pages_count": 10,
        "copies": 1,
        "color_mode": "black_white",
        "paper_size": "A4",
        "sides": "double_sided",
        "binding": "none",
    }
    return {**base, **over}


@pytest.fixture
def counter_prices(prices):
    return None


@pytest.mark.django_db
def test_counter_order_stores_duplex_flip_and_page_range(counter_prices, staff):
    payload = {"items": [line(duplex_flip="short", page_range="1-5, 8,10-11", pages_count=8)]}
    quote = api(staff).post(QUOTE, payload, format="json")
    assert quote.status_code == 200, quote.json()
    assert quote.json()["data"]["items"][0]["pages_count"] == 8

    response = api(staff).post(COUNTER, payload, format="json", HTTP_IDEMPOTENCY_KEY="counter-flip-1")
    assert response.status_code == 201, response.json()
    order = PrintOrder.objects.get(pk=response.json()["data"]["id"])
    item = order.items.get()
    assert item.duplex_flip == "short_edge" and item.page_range == "1-5,8,10-11"
    assert item.pages_count == 8 and item.sheets_count == 4
    assert item.pricing_snapshot["duplex_flip"] == "short_edge"
    assert item.pricing_snapshot["page_range"] == "1-5,8,10-11"
    from .jobs import item_options

    assert item_options(item)["duplex_flip"] == "short_edge" and item_options(item)["page_range"] == "1-5,8,10-11"


@pytest.mark.django_db
def test_counter_pages_derived_from_the_range_when_omitted(counter_prices, staff):
    payload = {"items": [line(page_range="3-6,9")]}
    del payload["items"][0]["pages_count"]
    response = api(staff).post(QUOTE, payload, format="json")
    assert response.status_code == 200, response.json()
    assert response.json()["data"]["items"][0]["pages_count"] == 5


@pytest.mark.django_db
def test_counter_defaults_keep_the_long_edge(counter_prices, staff):
    response = api(staff).post(COUNTER, {"items": [line(duplex_flip="")]}, format="json", HTTP_IDEMPOTENCY_KEY="k-long")
    assert response.status_code == 201
    item = PrintOrder.objects.get(pk=response.json()["data"]["id"]).items.get()
    assert item.duplex_flip == "long_edge" and item.page_range == ""


@pytest.mark.django_db
@pytest.mark.parametrize(
    "extra",
    [
        {"sides": "one_sided", "duplex_flip": "long"},
        {"duplex_flip": "diagonal"},
        {"page_range": "1-5,x"},
        {"page_range": "5-1"},
        {"page_range": "3-6,5"},
        {"page_range": "8,1-3"},
        {"page_range": "0-2"},
        {"page_range": "1" * 201},
        {"page_range": "1-5", "pages_count": 4},
    ],
)
def test_counter_rejects_bad_options(counter_prices, staff, extra):
    response = api(staff).post(QUOTE, {"items": [line(**extra)]}, format="json")
    assert response.status_code == 400, response.json()


@pytest.mark.django_db
def test_counter_mismatch_message_is_clear_arabic(counter_prices, staff):
    response = api(staff).post(QUOTE, {"items": [line(page_range="1-5", pages_count=4)]}, format="json")
    assert response.status_code == 400
    assert "لا يطابق نطاق الصفحات" in str(response.json())


# ---- orders list date filters -----------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_orders_filter_by_created_dates(counter_prices, staff):
    client = api(staff)
    ids = []
    for key in ("a", "b", "c"):
        response = client.post(COUNTER, {"items": [line()]}, format="json", HTTP_IDEMPOTENCY_KEY=f"date-{key}")
        ids.append(response.json()["data"]["id"])
    now = timezone.now()
    for order_id, days in zip(ids, (10, 5, 0), strict=True):
        PrintOrder.objects.filter(pk=order_id).update(created_at=now - timedelta(days=days))
    day = lambda n: (now - timedelta(days=n)).date().isoformat()  # noqa: E731

    def found(query):
        return {row["id"] for row in rows(client.get(f"{ORDERS}?{query}"))}

    assert found(f"created_after={day(6)}") == {ids[1], ids[2]}
    assert found(f"created_before={day(6)}") == {ids[0]}
    assert found(f"created_after={day(5)}&created_before={day(5)}") == {ids[1]}
    assert client.get(f"{ORDERS}?created_after=yesterday").status_code == 400
