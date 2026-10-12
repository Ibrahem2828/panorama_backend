"""Shared view-level helpers for the optional ``Idempotency-Key`` protocol.

The durable protocol lives in ``apps.product.services.IdempotencyService``. These helpers turn its outcomes into
API behaviour so every write endpoint reacts identically:

* no header            -> the write simply proceeds (the key is optional);
* same key + same body -> the stored response is replayed and the write is NOT repeated;
* same key + other body-> 409 ``IDEMPOTENCY_KEY_REUSED``;
* same key still running-> 409 ``idempotency_in_progress``;
* oversized key         -> 400.

Usage::

    def post(self, request):
        def write():
            ...perform the write...
            return success_response(...)

        return run_idempotent(request, "my-endpoint", write)

A write that is rejected with a 4xx API error (validation, permission, not found) releases the key, so the client
can correct the request and retry with the same key instead of being told it is "in progress" or "reused".
Unknown/5xx failures keep the key reserved until the protocol's in-progress timeout lets a retry take it over.

The endpoint name scopes the key (together with the actor), so a key reused on a different endpoint or for a
different object (include the object id in ``endpoint``) never collides.
"""

from __future__ import annotations

from collections.abc import Callable

from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response

from apps.common.exceptions import IdempotencyInProgress, IdempotencyKeyReused


def begin_idempotent(request, endpoint: str):
    """Return ``(decision, replay_response)``; ``replay_response`` is not None when the request was already served."""
    from apps.product.services import IdempotencyService  # local import: avoids a common <-> product import cycle

    try:
        decision = IdempotencyService.begin(request, endpoint=endpoint)
    except ValueError as exc:
        raise (
            IdempotencyKeyReused()
            if "different request" in str(exc)
            else ValidationError({"Idempotency-Key": str(exc)})
        ) from exc
    except RuntimeError as exc:
        raise IdempotencyInProgress() from exc
    if decision.replay_body is not None and decision.replay_status is not None:
        return decision, Response(decision.replay_body, status=decision.replay_status)
    return decision, None


def complete_idempotent(decision, response) -> None:
    """Store the finished response so a retry with the same key and body replays it."""
    from apps.product.services import IdempotencyService

    IdempotencyService.complete(decision, response)


def _release(decision) -> None:
    if not decision.record_id:
        return
    from apps.product.models import IdempotencyRecord

    IdempotencyRecord.objects.filter(pk=decision.record_id, response_status__isnull=True).delete()


def run_idempotent(request, endpoint: str, handler: Callable[[], Response]) -> Response:
    """Run ``handler`` under the idempotency protocol (see the module docstring)."""
    decision, replay = begin_idempotent(request, endpoint)
    if replay is not None:
        return replay
    try:
        response = handler()
    except APIException as exc:
        if exc.status_code < 500:
            _release(decision)
        raise
    complete_idempotent(decision, response)
    return response
