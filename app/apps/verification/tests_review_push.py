"""A staff decision on a verification request creates the in-app notification through NotificationService, so the
student also gets a (browser/mobile) push for approved, rejected and needs-update outcomes."""

from __future__ import annotations

import json
from unittest import mock

import pytest
from rest_framework.test import APIClient

from apps.notifications.models import Notification, NotificationType
from apps.verification import tests_card

world = tests_card.world  # reuse the shared verification fixture
PUSH_DELAY = "apps.notifications.tasks.deliver_push_notification.delay"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("action", "status_value", "body"),
    [
        ("approve", "approved", {}),
        ("reject", "rejected", {"rejection_reason": "Card is unreadable"}),
        ("needs-update", "needs_update", {"rejection_reason": "Please upload a clearer card"}),
    ],
)
def test_review_decision_notifies_the_student_and_queues_a_push(
    world,
    settings,
    django_capture_on_commit_callbacks,
    action,
    status_value,
    body,
):
    settings.WEB_PUSH_ENABLED = True
    verification = world.make_request(world.owner, b"x", "card.png")
    staff = APIClient()
    staff.force_authenticate(world.reviewer)
    with mock.patch(PUSH_DELAY) as delay:
        with django_capture_on_commit_callbacks(execute=True):
            response = staff.post(f"/api/v1/dashboard/verifications/{verification.id}/{action}/", body, format="json")
    assert response.status_code == 200, response.content
    notification = Notification.objects.get(user=world.owner, type=NotificationType.VERIFICATION)
    assert notification.data == {"verification_request_id": verification.id, "status": status_value}
    delay.assert_called_once()
    assert delay.call_args.args[0] == world.owner.id
    pushed = delay.call_args.args[3]
    assert pushed["status"] == status_value and pushed["notification_id"] == notification.pk
    assert pushed["type"] == NotificationType.VERIFICATION
    assert "web" in delay.call_args.kwargs["channels"]
    # Localized copy and the deep link travel with the notification and the push payload.
    assert notification.deep_link == ("/home" if action == "approve" else "/setup/verification/status")
    assert notification.title_ar == notification.title and notification.title_en
    assert notification.title_en.isascii() and notification.body_en
    assert pushed["title_en"] == notification.title_en and pushed["body_en"] == notification.body_en
    assert pushed["deep_link"] == notification.deep_link
    if action == "approve":
        assert notification.title_en == "Your account is verified"
        assert notification.body_en == "You can now use the verified-student features in Panorama."
    else:
        assert notification.body_en == notification.body_ar == body["rejection_reason"]
    from apps.notifications.services import WebPushService

    payload = json.loads(WebPushService.build_payload(delay.call_args.args[1], delay.call_args.args[2], pushed))
    assert payload["data"]["url"] == notification.deep_link
    assert payload["data"]["title_en"] == notification.title_en
