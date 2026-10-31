from __future__ import annotations

import logging
import time

from .activity import SystemActivityService

logger = logging.getLogger(__name__)


class SystemActivityMiddleware:
    """Record authenticated write requests (and 401/403 denials) of ``/api/`` into ``SystemActivity``.

    DRF authenticates inside the view and copies the user onto the Django request, so the user is read AFTER the
    response is built. Recording is best effort and can never change or break the response. What is skipped lives in
    ``apps.audit.activity_registry.SKIP_ROUTES``. Request bodies, headers other than the user agent and query strings
    are never read.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = time.perf_counter()
        response = self.get_response(request)
        try:
            if request.path.startswith("/api/"):
                user = getattr(request, "user", None)
                if user is not None and getattr(user, "is_authenticated", False):
                    SystemActivityService.record_request(
                        request, response, user=user, duration_ms=int((time.perf_counter() - started) * 1000)
                    )
        except Exception:  # noqa: BLE001
            logger.exception("system_activity_middleware_failed", extra={"event": "system_activity_middleware_failed"})
        return response
