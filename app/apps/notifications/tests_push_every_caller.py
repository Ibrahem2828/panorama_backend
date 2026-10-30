"""Audit: every producer of a Notification queues a push attempt when push is on and nothing suppresses it.

Producers covered through their real service entry points: support (new ticket -> staff, staff reply, status change),
group membership review, finance low-stock alert, campaigns (bulk), ``notify_staff`` and the chat fan-out. The
verification review has its own test (``apps/verification/tests_review_push.py``). Quiet hours, push off and channel off
stay valid reasons to hold the push back; the in-app notification is always kept.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from unittest import mock

import pytest
from django.test import override_settings

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.finance.models import InventoryItem, StockMovementType
from apps.finance.services import InventoryService
from apps.groups.models import Group, GroupMembership, GroupMembershipStatus
from apps.groups.services import GroupMembershipService
from apps.notifications.models import Notification, NotificationPreference, NotificationType
from apps.notifications.services import NotificationService, burst_window_seconds
from apps.notifications.staff import notify_staff
from apps.support.models import SupportTicketStatus
from apps.support.services import SupportTicketService
from apps.universities.models import University

PUSH_DELAY = "apps.notifications.tasks.deliver_push_notification.delay"
PUSH = {"PUSH_NOTIFICATIONS_ENABLED": True}


def _user(role, n: int) -> User:
    return User.objects.create_user(
        full_name=f"Audit {role} {n}",
        email=f"audit{n}@example.test",
        phone_number=f"+96398800{n:04d}",
        password="StrongPass123!",
        role=role,
    )


@pytest.fixture
def world(db):
    admin = _user(UserRole.ADMIN, 1)
    student = _user(UserRole.STUDENT, 2)
    university = University.objects.create(name="Audit University", code="AUU")
    StudentProfile.objects.create(
        user=student, university=university, verification_status=StudentVerificationStatus.APPROVED
    )
    group = Group.objects.create(name="Audit group", university=university, created_by=admin)
    return {"admin": admin, "student": student, "group": group}


def _pushed_to(delay) -> list[int]:
    return [call.args[0] for call in delay.call_args_list]


@override_settings(**PUSH)
def test_support_ticket_flow_pushes_staff_and_the_member(world, django_capture_on_commit_callbacks):
    admin, student = world["admin"], world["student"]
    with mock.patch(PUSH_DELAY) as delay:
        with django_capture_on_commit_callbacks(execute=True):
            ticket = SupportTicketService.create_ticket(student, "technical", "Help", "Please help")
        assert admin.id in _pushed_to(delay)  # staff hear about the new ticket

        delay.reset_mock()
        with django_capture_on_commit_callbacks(execute=True):
            SupportTicketService.add_message(ticket, admin, "On it")
        assert _pushed_to(delay) == [student.id]  # the member hears about the reply

        delay.reset_mock()
        with django_capture_on_commit_callbacks(execute=True):
            SupportTicketService.update_status(ticket, SupportTicketStatus.IN_PROGRESS, admin)
        assert _pushed_to(delay) == [student.id]


@override_settings(**PUSH)
def test_group_membership_review_pushes_the_student(world, django_capture_on_commit_callbacks):
    membership = GroupMembership.objects.create(
        group=world["group"], user=world["student"], status=GroupMembershipStatus.PENDING
    )
    with mock.patch(PUSH_DELAY) as delay:
        with django_capture_on_commit_callbacks(execute=True):
            GroupMembershipService.review(membership, world["admin"], GroupMembershipStatus.APPROVED)
    assert _pushed_to(delay) == [world["student"].id]


@override_settings(**PUSH)
def test_finance_low_stock_alert_pushes_printing_staff(world, django_capture_on_commit_callbacks):
    cashier = _user(UserRole.PRINT_STAFF, 3)
    paper = InventoryItem.objects.create(
        name="Paper", category="paper", unit="ream", quantity=Decimal("8"), reorder_level=Decimal("5")
    )
    with mock.patch(PUSH_DELAY) as delay:
        with django_capture_on_commit_callbacks(execute=True):
            InventoryService.record_movement(paper, StockMovementType.USAGE, Decimal("4"))
    assert cashier.id in _pushed_to(delay)


@override_settings(**PUSH)
def test_notify_staff_pushes_every_capable_staff_member(world, django_capture_on_commit_callbacks):
    other_admin = _user(UserRole.ADMIN, 4)
    with mock.patch(PUSH_DELAY) as delay:
        with django_capture_on_commit_callbacks(execute=True):
            created = notify_staff("support_ticket", "T", "B", object_id=99)
    assert created >= 2
    assert {world["admin"].id, other_admin.id} <= set(_pushed_to(delay))


@override_settings(**PUSH)
def test_bulk_campaign_pushes_each_allowed_recipient_and_skips_opted_out(world, django_capture_on_commit_callbacks):
    muted = _user(UserRole.STUDENT, 5)
    NotificationPreference.objects.create(user=muted, push_enabled=False)
    with mock.patch(PUSH_DELAY) as delay:
        with django_capture_on_commit_callbacks(execute=True):
            NotificationService.create_bulk_notifications([world["student"], muted], "T", "B", NotificationType.SYSTEM)
    assert _pushed_to(delay) == [world["student"].id]
    assert Notification.objects.filter(user=muted).exists()  # the in-app notification is kept


@override_settings(**PUSH)
def test_push_off_holds_back_the_push_but_keeps_the_notification(world, django_capture_on_commit_callbacks):
    NotificationPreference.objects.create(user=world["student"], push_enabled=False)
    with mock.patch(PUSH_DELAY) as delay:
        with django_capture_on_commit_callbacks(execute=True):
            NotificationService.create_notification(world["student"], "T", "B", NotificationType.SUPPORT)
    assert delay.call_count == 0
    assert Notification.objects.filter(user=world["student"]).count() == 1


@override_settings(**PUSH, PUSH_BURST_WINDOW_SECONDS=0)
def test_burst_window_zero_pushes_every_staff_notification(world, django_capture_on_commit_callbacks):
    assert burst_window_seconds() == 0
    with mock.patch(PUSH_DELAY) as delay:
        with django_capture_on_commit_callbacks(execute=True):
            for n in range(3):
                NotificationService.create_notification(
                    world["admin"], "T", "B", NotificationType.GROUP, data={"kind": "enrollment", "object_id": n}
                )
    assert delay.call_count == 3


def test_notifications_are_only_created_through_the_push_aware_services():
    """A direct ``Notification.objects.create``/``bulk_create`` outside the services would skip the push."""

    root = Path(__file__).resolve().parents[1]
    allowed = {"notifications/services.py"}
    pattern = re.compile(r"\bNotification\.objects\.(create|bulk_create|get_or_create|update_or_create)\(")
    offenders = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        if "/migrations/" in relative or "/tests" in relative or relative in allowed:
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(relative)
    assert not offenders, f"These modules create a Notification without a push attempt: {offenders}"
