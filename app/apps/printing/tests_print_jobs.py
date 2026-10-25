"""Dashboard printing: queue jobs, the print-ready PDF, job statuses; options and ready products for the customer."""

from __future__ import annotations

import io
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from pypdf import PdfReader, PdfWriter
from rest_framework.test import APIClient

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.audit.models import AuditAction, AuditLog

from .models import (
    PrintBindingPrice,
    PrintCoverOption,
    PrintOrder,
    PrintOrderStatus,
    ReadyProduct,
)
from .services import PrintOrderService, PrintStatusService


def make_pdf(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def make_user(role, email, phone):
    return User.objects.create_user(
        full_name=email.split("@")[0], email=email, phone_number=phone, password="StrongPass123!", role=role
    )


def api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def prices(db):
    call_command("seed_print_finance_defaults")


@pytest.fixture
def staff(db):
    return make_user(UserRole.PRINT_STAFF, "jobs-staff@example.com", "+963944490001")


@pytest.fixture
def customer(db):
    return make_user(UserRole.NORMAL_USER, "jobs-customer@example.com", "+963944490002")


@pytest.fixture
def order(prices, customer, staff):
    created = PrintOrderService.create_order(
        customer,
        [
            {
                "uploaded_file": SimpleUploadedFile("lecture.pdf", make_pdf(8), content_type="application/pdf"),
                "copies": 2,
                "color_mode": "black_white",
                "paper_size": "A4",
                "sides": "double_sided",
                "binding": "none",
                "pages_per_sheet": 2,
                "collate": True,
            }
        ],
    )
    for status in (PrintOrderStatus.UNDER_REVIEW, PrintOrderStatus.ACCEPTED):
        PrintStatusService.change_status(created, status, staff)
    created.refresh_from_db()
    return created


JOBS = "/api/v1/dashboard/printing/orders/{}/print-jobs/"


@pytest.mark.django_db
def test_queue_prepare_and_print_an_order(order, staff):
    client = api(staff)
    response = client.post(JOBS.format(order.pk), {}, format="json")
    assert response.status_code == 201, response.json()
    [job] = response.json()["data"]
    assert job["status"] == "queued" and job["sides_printed"] == 8 and job["sheets"] == 4
    assert job["options"]["pages_per_sheet"] == 2 and job["options"]["double_sided"] is True

    pdf = client.get(f"/api/v1/dashboard/printing/print-jobs/{job['id']}/file/")
    assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf"
    assert pdf["Cache-Control"].startswith("private, no-store")
    reader = PdfReader(io.BytesIO(pdf.content))
    assert len(reader.pages) == 8  # ceil(8/2/2) = 2 sheets per copy x 2 copies, both faces each
    assert (round(float(reader.pages[0].mediabox.width)), round(float(reader.pages[0].mediabox.height))) == (842, 595)
    assert AuditLog.objects.filter(action=AuditAction.PRINT_FILE_PREPARED).exists()

    started = client.patch(
        f"/api/v1/dashboard/printing/print-jobs/{job['id']}/status/",
        {"status": "printing", "printer_name": "HP"},
        format="json",
    )
    assert started.status_code == 200 and started.json()["data"]["printer_name"] == "HP"
    order.refresh_from_db()
    assert order.status == PrintOrderStatus.PRINTING  # the customer sees progress

    done = client.patch(f"/api/v1/dashboard/printing/print-jobs/{job['id']}/status/", {"status": "done"}, format="json")
    assert done.json()["data"]["status"] == "done" and done.json()["data"]["finished_at"]
    assert (
        client.patch(
            f"/api/v1/dashboard/printing/print-jobs/{job['id']}/status/", {"status": "printing"}, format="json"
        ).status_code
        == 409
    )


@pytest.mark.django_db
def test_failed_jobs_can_be_requeued_and_the_queue_is_filterable(order, staff):
    client = api(staff)
    [job] = client.post(JOBS.format(order.pk), {}, format="json").json()["data"]
    url = f"/api/v1/dashboard/printing/print-jobs/{job['id']}/status/"
    assert (
        client.patch(url, {"status": "failed", "error": "Paper jam"}, format="json").json()["data"]["error"]
        == "Paper jam"
    )
    queued = client.get("/api/v1/dashboard/printing/print-jobs/?status=queued").json()["data"]
    assert (queued["results"] if isinstance(queued, dict) else queued) == []
    assert client.post(f"/api/v1/dashboard/printing/print-jobs/{job['id']}/retry/").json()["data"]["status"] == "queued"
    again = client.get("/api/v1/dashboard/printing/print-jobs/?status=queued").json()["data"]
    assert len(again["results"] if isinstance(again, dict) else again) == 1


@pytest.mark.django_db
def test_students_cannot_print_and_finished_orders_cannot_be_sent(order, staff, customer):
    assert api(customer).post(JOBS.format(order.pk), {}, format="json").status_code == 403
    for status in (PrintOrderStatus.PRINTING, PrintOrderStatus.READY, PrintOrderStatus.DELIVERED):
        PrintStatusService.change_status(order, status, staff)
    assert api(staff).post(JOBS.format(order.pk), {}, format="json").status_code == 400


@pytest.mark.django_db
def test_only_files_of_the_order_can_be_picked(order, staff):
    assert api(staff).post(JOBS.format(order.pk), {"item_ids": [999999]}, format="json").status_code == 400
    assert api(staff).post(JOBS.format(order.pk), {"item_ids": "x"}, format="json").status_code == 400


# ---- options and ready products for the customer -----------------------------------------------------------------------------------


@pytest.mark.django_db
def test_options_endpoint_lists_configured_bindings_and_covers(prices, customer):
    PrintBindingPrice.objects.create(
        binding="hard_cover", name="تجليد كرتوني", price_per_copy=Decimal("500"), currency="SYP"
    )
    PrintCoverOption.objects.create(
        code="red", name="أحمر", color_hex="#B91C1C", price_per_copy=Decimal("50"), currency="SYP"
    )
    PrintCoverOption.objects.create(
        code="old", name="قديم", price_per_copy=Decimal("1"), currency="SYP", is_active=False
    )
    data = api(customer).get("/api/v1/printing/options/").json()["data"]
    assert data["pages_per_sheet"] == [1, 2, 4, 6, 9, 16]
    codes = {row["code"] for row in data["binding_types"]}
    assert {"spiral", "staple", "hard_cover"} <= codes
    assert [row["code"] for row in data["covers"]] == ["red"]


@pytest.mark.django_db
def test_ready_product_is_ordered_at_its_fixed_price_and_ignores_options(prices, customer, staff):
    product = ReadyProduct.objects.create(
        name="دفتر تشريح الأسنان",
        pages_count=120,
        paper_size="A4",
        sides="double_sided",
        binding="spiral",
        price=Decimal("5500"),
        currency="SYP",
    )
    ReadyProduct.objects.create(name="مخفي", price=Decimal("1"), is_active=False)
    listing = api(customer).get("/api/v1/printing/products/").json()["data"]
    rows = listing["results"] if isinstance(listing, dict) else listing
    assert [row["name"] for row in rows] == ["دفتر تشريح الأسنان"] and "source_file" not in rows[0]

    client = api(customer)
    body = {"items": [{"product": product.pk, "copies": 2}]}
    quote = client.post("/api/v1/printing/quote/", body, format="json")
    assert quote.status_code == 200, quote.json()
    assert Decimal(str(quote.json()["data"]["exact_total"])) == Decimal("11000")
    created = client.post("/api/v1/printing/orders/", body, format="json")
    assert created.status_code == 201, created.json()
    item = PrintOrder.objects.get(pk=created.json()["data"]["id"]).items.get()
    assert (
        item.product_id == product.pk
        and item.binding == "spiral"
        and item.pages_count == 120
        and item.sheets_count == 120
    )
    assert item.original_file_name == "دفتر تشريح الأسنان"
    assert client.post("/api/v1/printing/quote/", {"items": [{"product": 424242}]}, format="json").status_code == 400


@pytest.mark.django_db
def test_dashboard_can_manage_covers_wire_specs_and_products(staff):
    client = api(staff)
    cover = client.post(
        "/api/v1/dashboard/printing/covers/",
        {"code": "navy", "name": "كحلي", "color_hex": "#1E3A8A", "price_per_copy": "100", "currency": "SYP"},
        format="json",
    )
    assert cover.status_code == 201, cover.json()
    assert (
        client.post(
            "/api/v1/dashboard/printing/covers/",
            {"code": "bad", "name": "x", "color_hex": "red", "currency": "SYP"},
            format="json",
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/v1/dashboard/printing/wire-specs/",
            {"min_sheets": 1, "max_sheets": 40, "weight_grams": "18.5"},
            format="json",
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/v1/dashboard/printing/wire-specs/",
            {"min_sheets": 50, "max_sheets": 40, "weight_grams": "18.5"},
            format="json",
        ).status_code
        == 400
    )
    product = client.post(
        "/api/v1/dashboard/printing/products/",
        {"name": "دفتر", "price": "3000", "pages_count": 40, "binding": "staple"},
        format="json",
    )
    assert product.status_code == 201, product.json()
    assert (
        api(make_user(UserRole.NORMAL_USER, "n@example.com", "+963944490009"))
        .get("/api/v1/dashboard/printing/products/")
        .status_code
        == 403
    )
