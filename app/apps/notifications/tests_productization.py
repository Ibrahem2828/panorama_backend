from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.product.models import DeviceInstallation

from .models import Notification, NotificationPreference, NotificationType
from .services import NotificationService, PushNotificationService


@pytest.fixture
def user(db):
    return User.objects.create_user(
        full_name="Notification User",
        email="notification@example.test",
        phone_number="+963955222221",
        password="StrongPass123!",
    )


@pytest.fixture
def client(user):
    api_client = APIClient()
    api_client.force_authenticate(user)
    return api_client


def test_preferences_filter_expired_notifications_and_protect_updates(client, user):
    Notification.objects.create(user=user, title="Current", body="Current")
    Notification.objects.create(
        user=user, title="Expired", body="Expired", expires_at=timezone.now() - timedelta(seconds=1)
    )
    listing = client.get("/api/v1/notifications/")
    assert listing.status_code == 200
    assert listing.data["data"]["count"] == 1

    update = client.patch(
        "/api/v1/notifications/preferences/",
        {"push_enabled": False, "disabled_types": [NotificationType.SUPPORT]},
        format="json",
    )
    assert update.status_code == 200
    preference = NotificationPreference.objects.get(user=user)
    assert preference.push_enabled is False
    assert preference.allows(NotificationType.SUPPORT) is False
    assert NotificationService.create_notification(user, "Support", "private", type=NotificationType.SUPPORT) is None


def test_notification_service_uses_non_revoked_installation_push_token(user):
    DeviceInstallation.objects.create(
        user=user,
        installation_id="f5b74879-58ea-4fce-b8f6-b8df75beb3db",
        platform="android",
        push_token="ExponentPushToken[notification-product-test]",
    )
    with patch.object(PushNotificationService, "_send_expo", return_value=1) as sender:
        assert PushNotificationService.send_to_user(user, "Title", "Body") is True
    assert sender.call_args.args[0] == ["ExponentPushToken[notification-product-test]"]


def test_dashboard_campaign_requires_product_capability_and_creates_notifications(client, user):
    forbidden = client.post(
        "/api/v1/dashboard/notifications/campaign/", {"user_ids": [user.id], "title": "A", "body": "B"}, format="json"
    )
    assert forbidden.status_code == 403

    admin = User.objects.create_user(
        full_name="Admin",
        email="campaign-admin@example.test",
        phone_number="+963955222222",
        password="StrongPass123!",
        role=UserRole.ADMIN,
    )
    api_client = APIClient()
    api_client.force_authenticate(admin)
    created = api_client.post(
        "/api/v1/dashboard/notifications/campaign/",
        {"user_ids": [user.id], "title": "Campaign", "body": "Message", "deep_link": "/notifications"},
        format="json",
    )
    assert created.status_code == 201
    assert Notification.objects.filter(user=user, title="Campaign").exists()


@override_settings(PUSH_NOTIFICATIONS_ENABLED=True)
def test_bulk_notification_only_queues_recipients_allowed_by_preferences(user):
    NotificationPreference.objects.create(user=user, in_app_enabled=False)
    with patch("apps.notifications.tasks.deliver_push_notification.delay") as delay:
        assert NotificationService.create_bulk_notifications([user], "No", "No") == []
    delay.assert_not_called()
