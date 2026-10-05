from __future__ import annotations

from unittest import mock

import pytest
from django.conf import settings
from django.db import OperationalError
from rest_framework.test import APIClient


def test_every_beat_entry_points_at_a_registered_task():
    from config.celery import app

    app.loader.import_default_modules()
    missing = [name for name, entry in settings.CELERY_BEAT_SCHEDULE.items() if entry["task"] not in app.tasks]
    assert missing == []


def test_retention_purge_is_scheduled_and_runs_the_management_command():
    from apps.common.tasks import purge_expired_sensitive_data

    assert "purge-expired-sensitive-data" in settings.CELERY_BEAT_SCHEDULE
    with mock.patch("apps.common.tasks.call_command") as call:
        purge_expired_sensitive_data()
    call.assert_called_once_with("purge_expired_sensitive_data")


@pytest.mark.django_db
def test_db_health_reports_an_outage_as_a_controlled_503_with_a_request_id():
    with mock.patch("apps.common.health_views.connection") as connection:
        connection.cursor.side_effect = OperationalError("down")
        response = APIClient().get("/api/v1/health/db/")
    assert response.status_code == 503
    assert response.data["code"] == "SERVICE_NOT_READY"
    assert response.data.get("request_id")


def test_celery_is_configured_for_safe_redelivery():
    assert settings.CELERY_TASK_ACKS_LATE is True
    assert settings.CELERY_TASK_REJECT_ON_WORKER_LOST is True
    assert settings.CELERY_BROKER_TRANSPORT_OPTIONS["visibility_timeout"] > 960  # longer than the longest task


def test_websocket_origin_policy_blocks_foreign_browsers_but_not_native_clients():
    from asgiref.sync import async_to_sync
    from channels.testing import WebsocketCommunicator
    from config.asgi import application

    path = "/ws/v1/groups/1/chat/"

    async def handshake(headers=None):
        communicator = WebsocketCommunicator(application, path, headers=headers or [])
        connected, code = await communicator.connect()
        return connected, code

    # A native client (no Origin) reaches the consumer, which then refuses it for lack of a ticket (4401).
    assert async_to_sync(handshake)() == (False, 4401)
    # A browser on a foreign site is rejected during the handshake, before the consumer runs.
    connected, code = async_to_sync(handshake)([(b"origin", b"https://evil.example")])
    assert connected is False and code != 4401
    # A browser on an allowed host reaches the consumer as well.
    assert async_to_sync(handshake)([(b"origin", b"http://testserver")]) == (False, 4401)
