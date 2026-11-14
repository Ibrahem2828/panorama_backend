"""Option help («توضيح»): staff CRUD with audit, validation, student read of active entries only, seed idempotence."""

from __future__ import annotations

from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from PIL import Image
from rest_framework.test import APIClient

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.audit.models import AuditAction, AuditLog

from .models import PrintBinding, PrintColorMode, PrintOptionHelp, PrintPaperSize, PrintSides

DASH = "/api/v1/dashboard/printing/option-help/"
PUBLIC = "/api/v1/printing/option-help/"


def _user(role, n):
    return User.objects.create_user(
        full_name=f"Help {role}",
        email=f"help-{n}@example.test",
        phone_number=f"+96394448{n:04d}",
        password="StrongPass123!",
        role=role,
    )


def _client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def staff(db):
    return _client(_user(UserRole.PRINT_STAFF, 1))


@pytest.fixture
def student(db):
    return _client(_user(UserRole.STUDENT, 2))


def _png():
    buffer = BytesIO()
    Image.new("RGB", (10, 10), "red").save(buffer, "PNG")
    return SimpleUploadedFile("help.png", buffer.getvalue(), content_type="image/png")


def _payload(**extra):
    return {"topic": "binding", "key": "spiral", "title": "سلك", "body": "سطر أول\nسطر ثان", **extra}


def test_students_cannot_use_the_dashboard(student):
    assert student.get(DASH).status_code == 403
    assert student.post(DASH, _payload(), format="json").status_code == 403


def test_anonymous_cannot_read(db):
    assert APIClient().get(PUBLIC).status_code in (401, 403)


def test_staff_create_update_delete_with_audit(staff):
    created = staff.post(DASH, _payload(image_caption="شرح"), format="json")
    assert created.status_code == 201, created.content
    pk = created.json()["data"]["id"]
    assert AuditLog.objects.filter(action=AuditAction.PRINT_PRICING_CHANGED, target_type="PrintOptionHelp").count() == 1
    assert staff.patch(f"{DASH}{pk}/", {"title": "جديد"}, format="json").status_code == 200
    assert staff.delete(f"{DASH}{pk}/").status_code in (200, 204)
    assert AuditLog.objects.filter(action=AuditAction.PRINT_PRICING_CHANGED, target_id=str(pk)).count() == 3
    assert PrintOptionHelp.objects.get(pk=pk).is_deleted is True


@pytest.mark.parametrize(
    "topic", ["print_size", "print_colour", "print_sides", "binding", "cover", "spiral_wire", "extra:lamination"]
)
def test_valid_topics(staff, topic):
    assert staff.post(DASH, _payload(topic=topic, key=""), format="json").status_code == 201


@pytest.mark.parametrize("topic", ["nonsense", "extra:", "extra:Bad Slug", "Binding", "extra:" + "a" * 41])
def test_invalid_topic_rejected(staff, topic):
    assert staff.post(DASH, _payload(topic=topic), format="json").status_code == 400


def test_invalid_key_and_blank_text_rejected(staff):
    assert staff.post(DASH, _payload(key="bad key!"), format="json").status_code == 400
    assert staff.post(DASH, _payload(title="  "), format="json").status_code == 400
    assert staff.post(DASH, _payload(body=""), format="json").status_code == 400


def test_duplicate_topic_key_rejected_but_reusable_after_delete(staff):
    first = staff.post(DASH, _payload(), format="json").json()["data"]["id"]
    assert staff.post(DASH, _payload(), format="json").status_code == 400
    staff.delete(f"{DASH}{first}/")
    assert staff.post(DASH, _payload(), format="json").status_code == 201


def test_image_upload_validated(staff):
    ok = staff.post(DASH, {**_payload(), "image": _png()}, format="multipart")
    assert ok.status_code == 201, ok.content
    assert ok.json()["data"]["image_url"]
    fake = SimpleUploadedFile("x.png", b"not an image", content_type="image/png")
    bad = staff.post(DASH, {**_payload(key="staple"), "image": fake}, format="multipart")
    assert bad.status_code == 400


def test_public_read_only_active_and_filterable(staff, student):
    staff.post(DASH, _payload(), format="json")
    staff.post(DASH, _payload(key="staple", title="دبوس"), format="json")
    staff.post(DASH, _payload(topic="print_size", key="A4", is_active=False), format="json")
    staff.post(DASH, _payload(key=""), format="json")
    data = student.get(PUBLIC).json()["data"]
    assert data["count"] == 3
    assert {i["topic"] for i in data["items"]} == {"binding"}
    assert data["updated_at"]
    assert set(data["by_topic"]) == {"binding"}
    assert "is_active" not in data["items"][0]
    only = student.get(PUBLIC, {"topic": "binding", "key": "spiral"}).json()["data"]
    assert [i["key"] for i in only["items"]] == ["spiral"]
    topic_level = student.get(PUBLIC, {"topic": "binding", "key": ""}).json()["data"]
    assert [i["key"] for i in topic_level["items"]] == [""]


def test_public_read_supports_etag(staff, student):
    staff.post(DASH, _payload(), format="json")
    etag = student.get(PUBLIC)["ETag"]
    assert student.get(PUBLIC, HTTP_IF_NONE_MATCH=etag).status_code == 304
    staff.post(DASH, _payload(key="staple"), format="json")
    assert student.get(PUBLIC, HTTP_IF_NONE_MATCH=etag).status_code == 200


def test_seed_is_idempotent_covers_current_keys_and_keeps_edits(db):
    call_command("seed_print_option_help")
    total = PrintOptionHelp.objects.count()
    assert total > 15
    pairs = set(PrintOptionHelp.objects.values_list("topic", "key"))
    expected = (
        {("print_size", v) for v in PrintPaperSize.values}
        | {("print_colour", v) for v in PrintColorMode.values}
        | {("print_sides", v) for v in PrintSides.values}
        | {("binding", v) for v in PrintBinding.values}
        | {("cover", ""), ("spiral_wire", "")}
    )
    assert expected <= pairs
    row = PrintOptionHelp.objects.get(topic="binding", key="spiral")
    row.title = "معدّل"
    row.save()
    call_command("seed_print_option_help")
    assert PrintOptionHelp.objects.count() == total
    assert PrintOptionHelp.objects.get(pk=row.pk).title == "معدّل"
    for item in PrintOptionHelp.objects.all():
        item.full_clean()


def _with_image(staff, **extra):
    created = staff.post(DASH, {**_payload(**extra), "is_active": "true", "image": _png()}, format="multipart")
    assert created.status_code == 201, created.content
    return created.json()["data"]


def test_image_url_is_the_authenticated_api_route_not_media(staff, student):
    row = _with_image(staff)
    assert f"/api/v1/printing/option-help/{row['id']}/image/?v=" in row["image_url"]
    assert "/media/" not in row["image_url"]
    item = student.get(PUBLIC).json()["data"]["items"][0]
    assert f"/api/v1/printing/option-help/{item['id']}/image/?v=" in item["image_url"]


def test_image_requires_authentication(staff):
    row = _with_image(staff)
    assert APIClient().get(f"{PUBLIC}{row['id']}/image/").status_code in (401, 403)


def test_image_served_inline_with_headers_and_etag(staff, student):
    pk = _with_image(staff)["id"]
    response = student.get(f"{PUBLIC}{pk}/image/")
    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Cache-Control"] == "private, max-age=3600"
    assert b"".join(response.streaming_content).startswith(b"\x89PNG")
    etag = response["ETag"]
    again = student.get(f"{PUBLIC}{pk}/image/", HTTP_IF_NONE_MATCH=etag)
    assert again.status_code == 304
    assert again["ETag"] == etag


def test_inactive_or_deleted_image_is_404_for_students_but_staff_can_see_inactive(staff, student):
    pk = _with_image(staff)["id"]
    assert staff.patch(f"{DASH}{pk}/", {"is_active": False}, format="json").status_code == 200
    assert student.get(f"{PUBLIC}{pk}/image/").status_code == 404
    assert staff.get(f"{PUBLIC}{pk}/image/").status_code == 200
    assert staff.delete(f"{DASH}{pk}/").status_code in (200, 204)
    assert staff.get(f"{PUBLIC}{pk}/image/").status_code == 404


def test_entry_without_image_is_404(staff, student):
    pk = staff.post(DASH, _payload(), format="json").json()["data"]["id"]
    assert student.get(f"{PUBLIC}{pk}/image/").status_code == 404
