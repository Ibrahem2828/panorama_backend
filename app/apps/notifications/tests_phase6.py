"""Notification platform: categories and channels, quiet hours, burst collapse, staff inbox, receipts, health, quick access."""

from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta
from datetime import timezone as dt_timezone
from unittest import mock

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.notifications import categories
from apps.notifications.models import (
    DeviceToken,
    Notification,
    NotificationPreference,
    NotificationType,
    PushDelivery,
    PushDeliveryStatus,
)
from apps.notifications.services import NotificationService, PushNotificationService, PushReceiptService
from apps.notifications.staff import notify_staff

pytestmark = pytest.mark.django_db

URLOPEN = "apps.notifications.services.urllib_request.urlopen"
PUSH = {
    "PUSH_NOTIFICATIONS_ENABLED": True,
    "EXPO_PUSH_ENDPOINT": "https://exp.host/--/api/v2/push/send",
    "EXPO_PUSH_ALLOWED_HOSTS": frozenset({"exp.host"}),
}
TOKEN = "ExponentPushToken[aaaaaaaaaaaaaaaaaaaaaa]"
TOKEN_2 = "ExponentPushToken[bbbbbbbbbbbbbbbbbbbbbb]"


def _user(email, role=UserRole.STUDENT, n=1):
    return User.objects.create_user(
        full_name=email.split("@")[0],
        email=email,
        phone_number=f"+9639558{n:05d}",
        password="StrongPass123!",
        role=role,
    )


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def student():
    return _user("s@t.test", n=1)


@pytest.fixture
def admin():
    return _user("a@t.test", UserRole.ADMIN, n=2)


@pytest.fixture
def it():
    return _user("it@t.test", UserRole.IT_SUPPORT, n=3)


def _client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def _data(response):
    payload = response.data["data"]
    return payload["results"] if isinstance(payload, dict) and "results" in payload else payload


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self._raw = json.dumps(payload).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


# -- categories ----------------------------------------------------------------------------------------------------------


def test_category_comes_from_kind_first_then_type():
    assert categories.category_for(NotificationType.GROUP, {"kind": "enrollment"}) == "enrollment"
    assert categories.category_for(NotificationType.SYSTEM, {"kind": "purchase_reminder"}) == "purchases"
    assert categories.category_for(NotificationType.CHAT) == "chat"
    assert categories.category_for(NotificationType.PRINTING) == "printing"
    assert categories.category_for(NotificationType.ANNOUNCEMENT) == "general"


def test_android_channel_per_category():
    assert categories.android_channel_for("chat") == "chat"
    assert categories.android_channel_for("verification") == "verification"
    assert categories.android_channel_for("purchases") == "default"
    assert categories.preference_channel_for("enrollment") == "groups"
    assert categories.preference_channel_for("purchases") is None


@override_settings(**PUSH)
def test_push_carries_category_and_channel_id(student, django_capture_on_commit_callbacks):
    with mock.patch("apps.notifications.tasks.deliver_push_notification.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            NotificationService.create_notification(student, "T", "B", NotificationType.PRINTING)
    data = delay.call_args.args[3]
    assert data["category"] == "printing" and data["channel_id"] == "printing"
    assert Notification.objects.get(user=student).category == "printing"


# -- preferences ---------------------------------------------------------------------------------------------------------


def test_preferences_expose_channels_and_quiet_hours_with_old_fields_intact(student):
    client = _client(student)
    body = client.get("/api/v1/notifications/preferences/").data["data"]
    assert body["push_enabled"] is True and body["in_app_enabled"] is True
    assert body["channels"] == {"chat": True, "groups": True, "printing": True, "verification": True, "support": True}
    assert body["quiet_hours"] == {"enabled": False, "start": None, "end": None, "timezone": "Asia/Damascus"}

    response = client.patch(
        "/api/v1/notifications/preferences/",
        {"channels": {"chat": False}, "quiet_hours": {"enabled": True, "start": "22:00", "end": "07:00"}},
        format="json",
    )
    assert response.status_code == 200, response.data
    data = response.data["data"]
    assert data["channels"]["chat"] is False and data["channels"]["groups"] is True
    assert data["quiet_hours"] == {"enabled": True, "start": "22:00", "end": "07:00", "timezone": "Asia/Damascus"}


def test_preference_validation(student):
    client = _client(student)
    assert (
        client.patch("/api/v1/notifications/preferences/", {"channels": {"nope": False}}, format="json").status_code
        == 400
    )
    assert (
        client.patch(
            "/api/v1/notifications/preferences/", {"quiet_hours": {"enabled": True}}, format="json"
        ).status_code
        == 400
    )
    assert (
        client.patch(
            "/api/v1/notifications/preferences/",
            {"quiet_hours": {"enabled": True, "start": "22:00", "end": "07:00", "timezone": "Mars/Base"}},
            format="json",
        ).status_code
        == 400
    )


def test_quiet_hours_window_including_midnight_and_timezone():
    preference = NotificationPreference(quiet_enabled=True, quiet_start=time(22, 0), quiet_end=time(7, 0))
    damascus = dt_timezone(timedelta(hours=3))  # Asia/Damascus is UTC+3 all year
    assert preference.in_quiet_hours(datetime(2026, 1, 1, 23, 30, tzinfo=damascus))
    assert preference.in_quiet_hours(datetime(2026, 1, 2, 6, 59, tzinfo=damascus))
    assert not preference.in_quiet_hours(datetime(2026, 1, 2, 7, 0, tzinfo=damascus))
    assert not preference.in_quiet_hours(datetime(2026, 1, 2, 12, 0, tzinfo=damascus))
    # the same instant is "night" in Damascus but "afternoon" in UTC-9
    instant = datetime(2026, 1, 1, 20, 0, tzinfo=UTC)  # 23:00 in Damascus
    assert preference.in_quiet_hours(instant)
    preference.quiet_timezone = "Pacific/Honolulu"
    assert not preference.in_quiet_hours(instant)
    assert not NotificationPreference(quiet_enabled=False, quiet_start=time(22), quiet_end=time(7)).in_quiet_hours(
        instant
    )


@override_settings(**PUSH)
def test_channel_off_and_quiet_hours_hold_the_push_back_but_keep_the_notification(
    student, django_capture_on_commit_callbacks
):
    NotificationPreference.objects.create(user=student, channels={"printing": False})
    with mock.patch("apps.notifications.tasks.deliver_push_notification.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            notification = NotificationService.create_notification(student, "T", "B", NotificationType.PRINTING)
        assert notification is not None and delay.call_count == 0
        with django_capture_on_commit_callbacks(execute=True):
            NotificationService.create_notification(student, "T", "B", NotificationType.SUPPORT)
        assert delay.call_count == 1  # another channel still pushes
        NotificationPreference.objects.filter(user=student).update(
            quiet_enabled=True, quiet_start=time(0, 0), quiet_end=time(23, 59)
        )
        with django_capture_on_commit_callbacks(execute=True):
            NotificationService.create_notification(student, "T", "B", NotificationType.SUPPORT)
        assert delay.call_count == 1
    assert Notification.objects.filter(user=student).count() == 3


@override_settings(**PUSH)
def test_staff_bursts_collapse_into_one_push_but_members_notices_do_not(
    admin, student, django_capture_on_commit_callbacks
):
    with mock.patch("apps.notifications.tasks.deliver_push_notification.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            for n in range(3):
                NotificationService.create_notification(
                    admin, "T", "B", NotificationType.GROUP, data={"kind": "enrollment", "object_id": n}
                )
        assert delay.call_count == 1
        with django_capture_on_commit_callbacks(execute=True):
            for _ in range(3):
                NotificationService.create_notification(student, "T", "B", NotificationType.PRINTING)
        assert delay.call_count == 4  # a member's own updates always push
    assert Notification.objects.filter(user=admin).count() == 3  # nothing is lost in the inbox


# -- inbox ---------------------------------------------------------------------------------------------------------------


def test_summary_counts_unread_by_category_and_read_all_can_be_scoped(admin):
    for kind, ntype in (
        ("enrollment", "group"),
        ("enrollment", "group"),
        ("chat_report", "group"),
        ("support_ticket", "support"),
    ):
        NotificationService.create_notification(admin, "T", "B", ntype, data={"kind": kind})
    client = _client(admin)
    summary = client.get("/api/v1/notifications/summary/").data["data"]
    assert summary["unread"] == 4
    assert summary["by_category"] == {"enrollment": 2, "reports": 1, "support": 1}

    listed = _data(client.get("/api/v1/notifications/?category=enrollment"))
    assert len(listed) == 2 and {item["category"] for item in listed} == {"enrollment"}

    client.post("/api/v1/notifications/read-all/?category=enrollment")
    after = client.get("/api/v1/notifications/summary/").data["data"]
    assert after["unread"] == 2 and "enrollment" not in after["by_category"]
    assert len(_data(client.get("/api/v1/notifications/?is_read=false"))) == 2


def test_summary_is_private(admin, student):
    NotificationService.create_notification(admin, "T", "B", NotificationType.SUPPORT)
    assert _client(student).get("/api/v1/notifications/summary/").data["data"]["unread"] == 0


def test_notify_staff_reaches_only_people_who_hold_the_capability_and_never_twice(admin, student):
    printer = _user("p@t.test", UserRole.PRINT_STAFF, n=4)
    support = _user("sup@t.test", UserRole.SUPPORT_STAFF, n=5)
    assert notify_staff("print_order", "طلب", "نص", object_id=7, actor_id=student.pk) == 2  # admin + print staff
    assert Notification.objects.filter(user=printer, data__kind="print_order").count() == 1
    assert not Notification.objects.filter(user__in=[support, student]).exists()
    assert notify_staff("print_order", "طلب", "نص", object_id=7, actor_id=student.pk) == 0  # already told
    note = Notification.objects.get(user=printer)
    assert note.category == "printing" and note.deep_link == "/dashboard/printing?highlight=7"


def test_actor_is_not_notified_about_their_own_action(admin):
    assert notify_staff("chat_report", "بلاغ", "نص", object_id=1, actor_id=admin.pk) == 0


# -- events hook the inbox -----------------------------------------------------------------------------------------------


def test_support_ticket_lands_in_the_staff_inbox(admin, student, django_capture_on_commit_callbacks):
    from apps.support.models import SupportTicketCategory
    from apps.support.services import SupportTicketService

    with django_capture_on_commit_callbacks(execute=True):
        SupportTicketService.create_ticket(student, SupportTicketCategory.values[0], "مشكلة", "تفاصيل")
    note = Notification.objects.get(user=admin, data__kind="support_ticket")
    assert note.category == "support" and note.deep_link.startswith("/dashboard/support")


def test_chat_report_lands_in_the_staff_inbox():
    from apps.chat.models import Message
    from apps.chat.tests_handshake_tickets import _build_chat_fixture

    chat_admin, chat_student, group = _build_chat_fixture()
    message = Message.objects.create(group=group, sender=chat_admin, content="bad")
    response = _client(chat_student).post(
        f"/api/v1/groups/{group.pk}/messages/{message.pk}/report/", {"reason": "abuse"}, format="json"
    )
    assert response.status_code == 201, response.data
    note = Notification.objects.get(user=chat_admin, data__kind="chat_report")
    assert note.category == "reports" and note.deep_link.startswith("/dashboard/chat-reports")


# -- receipts ------------------------------------------------------------------------------------------------------------


@override_settings(**PUSH)
def test_sent_pushes_are_recorded_with_their_ticket(student):
    DeviceToken.objects.create(user=student, token=TOKEN, platform="android")
    with mock.patch(URLOPEN, return_value=FakeResponse({"data": [{"status": "ok", "id": "tkt-1"}]})):
        assert PushNotificationService._send_expo([TOKEN], "T", "B", {"notification_id": None, "category": "chat"}) == 1
    row = PushDelivery.objects.get()
    assert row.status == "sent" and row.ticket_id == "tkt-1" and row.token == TOKEN and row.category == "chat"
    assert len(row.token_hash) == 64


@override_settings(**PUSH)
def test_dead_tickets_are_recorded_without_the_raw_token(student):
    DeviceToken.objects.create(user=student, token=TOKEN, platform="android")
    ticket = {"status": "error", "details": {"error": "DeviceNotRegistered"}}
    with mock.patch(URLOPEN, return_value=FakeResponse({"data": [ticket]})):
        PushNotificationService._send_expo([TOKEN], "T", "B")
    row = PushDelivery.objects.get()
    assert row.status == "dead" and row.token == "" and row.error_code == "DeviceNotRegistered"


@override_settings(**PUSH)
def test_receipts_settle_deliveries_and_switch_dead_devices_off(student):
    live = DeviceToken.objects.create(user=student, token=TOKEN, platform="android")
    gone = DeviceToken.objects.create(user=student, token=TOKEN_2, platform="android")
    old = timezone.now() - timedelta(minutes=30)
    rows = []
    for ticket, token in (("t-ok", TOKEN), ("t-dead", TOKEN_2), ("t-wait", TOKEN), ("t-err", TOKEN)):
        row = PushDelivery.objects.create(ticket_id=ticket, token=token, channel="expo")
        PushDelivery.objects.filter(pk=row.pk).update(created_at=old)
        rows.append(row)
    receipts = {
        "t-ok": {"status": "ok"},
        "t-dead": {"status": "error", "details": {"error": "DeviceNotRegistered"}},
        "t-err": {"status": "error", "details": {"error": "MessageTooBig"}},
    }  # t-wait has no receipt yet
    with mock.patch(URLOPEN, return_value=FakeResponse({"data": receipts})) as opened:
        result = PushReceiptService.check_pending()
    assert opened.call_args.args[0].full_url.endswith("/getReceipts")
    assert result["delivered"] == 1 and result["dead"] == 1 and result["failed"] == 1 and result["checked"] == 3
    status = {row.ticket_id: row for row in PushDelivery.objects.all()}
    assert status["t-ok"].status == "delivered" and status["t-ok"].token == ""
    assert status["t-dead"].status == "dead" and status["t-err"].status == "failed"
    assert status["t-wait"].status == "sent" and status["t-wait"].token == TOKEN  # asked again next time
    live.refresh_from_db()
    gone.refresh_from_db()
    assert live.is_active is True and gone.is_active is False


@override_settings(**PUSH)
def test_young_deliveries_are_not_polled_and_forgotten_ones_expire():
    PushDelivery.objects.create(ticket_id="fresh", token=TOKEN, channel="expo")
    stale = PushDelivery.objects.create(ticket_id="stale", token=TOKEN, channel="expo")
    PushDelivery.objects.filter(pk=stale.pk).update(created_at=timezone.now() - timedelta(hours=30))
    with mock.patch(URLOPEN) as opened:
        result = PushReceiptService.check_pending()
    assert opened.call_count == 0 and result["expired"] == 1
    assert PushDelivery.objects.get(ticket_id="stale").status == PushDeliveryStatus.FAILED
    assert PushDelivery.objects.get(ticket_id="fresh").status == PushDeliveryStatus.SENT


@override_settings(**PUSH)
def test_unreachable_receipt_service_changes_nothing():
    row = PushDelivery.objects.create(ticket_id="x", token=TOKEN, channel="expo")
    PushDelivery.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(hours=1))
    with mock.patch(URLOPEN, side_effect=OSError("down")):
        assert PushReceiptService.check_pending()["checked"] == 0
    assert PushDelivery.objects.get(pk=row.pk).status == "sent"


# -- health + quick access -----------------------------------------------------------------------------------------------


def test_health_is_for_system_administrators_only(admin, it, student):
    PushDelivery.objects.create(status="delivered", category="chat", channel="expo")
    PushDelivery.objects.create(status="delivered", category="chat", channel="expo")
    PushDelivery.objects.create(status="failed", category="printing", error_code="MessageTooBig", channel="expo")
    PushDelivery.objects.create(status="sent", category="chat", channel="expo")
    old = PushDelivery.objects.create(status="dead", category="chat", error_code="DeviceNotRegistered", channel="expo")
    PushDelivery.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=3))

    assert _client(student).get("/api/v1/dashboard/notifications/health/").status_code == 403
    assert _client(admin).get("/api/v1/dashboard/notifications/health/").status_code == 403
    day = _client(it).get("/api/v1/dashboard/notifications/health/").data["data"]
    assert day["range"] == "24h" and day["pushes"]["total"] == 4
    assert day["pushes"]["delivered"] == 2 and day["pushes"]["failed"] == 1 and day["pushes"]["awaiting_receipt"] == 1
    assert day["pushes"]["delivery_rate"] == round(2 / 3, 4)
    assert day["top_errors"][0]["error_code"] == "MessageTooBig"
    week = _client(it).get("/api/v1/dashboard/notifications/health/?range=7d").data["data"]
    assert week["pushes"]["total"] == 5 and week["pushes"]["dead"] == 1
    assert _client(it).get("/api/v1/dashboard/notifications/health/?range=zzz").data["data"]["range"] == "24h"


def test_quick_access_only_counts_what_the_caller_may_act_on(admin, student):
    printer = _user("p@t.test", UserRole.PRINT_STAFF, n=4)
    NotificationService.create_notification(printer, "T", "B", NotificationType.PRINTING)
    admin_counts = _client(admin).get("/api/v1/dashboard/quick-access/").data["data"]["counts"]
    for key in (
        "enrollment_pending",
        "verification_pending",
        "chat_reports_open",
        "print_orders_new",
        "low_stock",
        "support_open",
        "purchase_reminders_due",
        "notifications_unread",
    ):
        assert key in admin_counts
    printer_counts = _client(printer).get("/api/v1/dashboard/quick-access/").data["data"]["counts"]
    assert set(printer_counts) == {"print_orders_new", "low_stock", "notifications_unread"}
    assert printer_counts["notifications_unread"] == 1
    assert _client(student).get("/api/v1/dashboard/quick-access/").status_code == 403
