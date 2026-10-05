"""Error tracking. Inert unless SENTRY_DSN is set, and it never sends request bodies or credentials."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_SENSITIVE_HEADERS = {"authorization", "cookie", "x-csrftoken", "x-viewer-session", "idempotency-key"}


def scrub_event(event: Any, hint: Any = None) -> Any:
    """Drop everything that can carry personal data or credentials before an event leaves the server."""

    request = event.get("request")
    if isinstance(request, dict):
        request.pop("data", None)
        request.pop("cookies", None)
        request.pop("query_string", None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {k: v for k, v in headers.items() if k.lower() not in _SENSITIVE_HEADERS}
    user = event.get("user")
    if isinstance(user, dict):
        event["user"] = {"id": user.get("id")} if user.get("id") is not None else {}
    return event


def init_sentry() -> bool:
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration

    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
        release=os.environ.get("SENTRY_RELEASE") or os.environ.get("IMAGE_TAG") or None,
        send_default_pii=False,
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.05")),
        max_request_body_size="never",
        before_send=scrub_event,
        integrations=[
            DjangoIntegration(),
            CeleryIntegration(),
            # Our JSON logger already carries request ids; only error-level records become events.
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
    )
    return True
