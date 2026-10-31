"""Recording of ``SystemActivity`` rows. Everything here is best effort: it never raises into a request."""

from __future__ import annotations

import logging
import re
from typing import Any

from django.db import transaction

from apps.common.request_utils import get_client_ip

from .activity_registry import is_skipped, label_for
from .models import LoginFailureReason, SystemActivity, SystemActivityKind

logger = logging.getLogger(__name__)

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
API_PREFIX = "api/v1/"

_CAPTURE_RE = re.compile(r"\(\?P<(\w+)>[^)]*\)")
_CONVERTER_RE = re.compile(r"<(?:\w+:)?(\w+)>")
_FORMAT_SUFFIX_RE = re.compile(r"\\\.\(\?P<format>.*$")
#: URL kwargs that are secrets or are not an object id; they never become ``object_id``.
_NON_OBJECT_KWARGS = frozenset({"token", "code", "kind", "permission_code", "format"})

LOGIN_ROUTE = "auth/login/"
LOGOUT_ROUTE = "auth/logout/"

_FAILURE_NOTES = {
    LoginFailureReason.INVALID_CREDENTIALS: "بيانات الدخول غير صحيحة",
    LoginFailureReason.PASSWORD_NOT_SET: "لم تُقبل الدعوة بعد",
    LoginFailureReason.INACTIVE_ACCOUNT: "الحساب معطّل",
    LoginFailureReason.UNVERIFIED: "الحساب غير موثّق",
    LoginFailureReason.LOCKED: "الحساب مقفل مؤقتًا بعد محاولات فاشلة متكررة",
}


def normalise_route(raw_route: str) -> str:
    """``api/v1/^dashboard/users/(?P<pk>[^/.]+)/$`` or ``.../<int:pk>/`` -> ``dashboard/users/{pk}/``."""

    route = _FORMAT_SUFFIX_RE.sub("", raw_route or "")
    route = _CAPTURE_RE.sub(lambda m: "{" + m.group(1) + "}", route)
    route = _CONVERTER_RE.sub(lambda m: "{" + m.group(1) + "}", route)
    route = route.replace("^", "").replace("$", "").replace("\\Z", "").replace("/?", "/")
    route = route.removeprefix(API_PREFIX)
    return route[:255]


def mask_identifier(identifier: object) -> str:
    """``asma@gmail.com`` -> ``as***@gmail.com``; ``+963912345123`` -> ``+9639***123``. Never the full value."""

    value = str(identifier or "").strip()
    if not value:
        return ""
    if "@" in value:
        local, _, domain = value.rpartition("@")
        keep = local[:2] if len(local) > 2 else local[:1]
        return f"{keep}***@{domain}"[:120]
    if len(value) >= 9:
        return f"{value[:5]}***{value[-3:]}"
    return f"{value[:2]}***"


def user_snapshot(user) -> dict[str, str]:
    if user is None or not getattr(user, "pk", None):
        return {"user_label": "", "role": ""}
    name = (getattr(user, "full_name", "") or "").strip()
    email = (getattr(user, "email", "") or "").strip()
    label = f"{name} ({email})" if name and email else (name or email)
    return {"user_label": label[:255], "role": str(getattr(user, "role", "") or "")[:32]}


def _object_from_kwargs(route: str, kwargs: dict[str, Any]) -> tuple[str, str]:
    """The addressed object: the LAST id-like URL kwarg (``messages/{message_id}`` beats ``groups/{group_id}``)."""

    chosen: tuple[str, str] | None = None
    for name, value in kwargs.items():
        if name in _NON_OBJECT_KWARGS or not (name == "pk" or name.endswith("_id") or name.endswith("_pk")):
            continue
        chosen = (name, str(value))
    if chosen is None:
        return "", ""
    name, value = chosen
    if len(value) > 64:
        return "", ""
    if name == "pk":
        # ``dashboard/groups/{pk}/approve/`` -> the collection right before the placeholder.
        before = [s for s in route[: route.find("{pk}")].split("/") if s]
        object_type = before[-1] if before else ""
    else:
        object_type = name.removesuffix("_id").removesuffix("_pk")
    return object_type[:64], value


def _write(**fields: Any) -> SystemActivity | None:
    try:
        with transaction.atomic():
            return SystemActivity.objects.create(**fields)
    except Exception:  # noqa: BLE001 - the activity trail must never break the request it describes
        logger.exception("system_activity_write_failed", extra={"event": "system_activity_write_failed"})
        return None


class SystemActivityService:
    @staticmethod
    def record_request(request, response, *, user, duration_ms: int) -> SystemActivity | None:
        """Called by the middleware for an authenticated request; decides whether it is worth a row."""

        try:
            match = getattr(request, "resolver_match", None)
            if match is None:
                return None
            route = normalise_route(getattr(match, "route", "") or "")
            if not route or is_skipped(route):
                return None
            status_code = int(response.status_code)
            denied = status_code in (401, 403)
            if not denied and request.method not in WRITE_METHODS:
                return None
            object_type, object_id = _object_from_kwargs(route, dict(getattr(match, "kwargs", {}) or {}))
            extra: dict[str, Any] = {}
            request_id = getattr(request, "request_id", None)
            if request_id:
                extra["request_id"] = str(request_id)[:128]
            data = getattr(response, "data", None)
            if status_code >= 400 and isinstance(data, dict) and isinstance(data.get("code"), str):
                extra["error_code"] = data["code"][:64]
            return _write(
                user=user,
                kind=SystemActivityKind.DENIED if denied else SystemActivityKind.REQUEST,
                method=request.method,
                route=route,
                status_code=status_code,
                action_label=label_for(request.method, route)[:255],
                object_type=object_type,
                object_id=object_id,
                ip=(get_client_ip(request) or "")[:64],
                user_agent=request.META.get("HTTP_USER_AGENT", "")[:200],
                duration_ms=duration_ms,
                extra=extra,
                **user_snapshot(user),
            )
        except Exception:  # noqa: BLE001
            logger.exception("system_activity_record_failed", extra={"event": "system_activity_record_failed"})
            return None

    @staticmethod
    def _auth_event(
        request, kind: str, *, user, status_code: int, label: str, route: str, extra: dict[str, Any]
    ) -> SystemActivity | None:
        try:
            return _write(
                user=user,
                kind=kind,
                method="POST",
                route=route,
                status_code=status_code,
                action_label=label,
                ip=(get_client_ip(request) or "")[:64],
                user_agent=request.META.get("HTTP_USER_AGENT", "")[:200],
                extra=extra,
                **user_snapshot(user),
            )
        except Exception:  # noqa: BLE001
            logger.exception("system_activity_record_failed", extra={"event": "system_activity_record_failed"})
            return None

    @classmethod
    def record_login_success(cls, request, user) -> SystemActivity | None:
        extra: dict[str, Any] = {"must_change_password": bool(getattr(user, "must_change_password", False))}
        return cls._auth_event(
            request,
            SystemActivityKind.LOGIN_SUCCESS,
            user=user,
            status_code=200,
            label="تسجيل الدخول",
            route=LOGIN_ROUTE,
            extra=extra,
        )

    @classmethod
    def record_login_failure(
        cls, request, *, identifier: object, reason: str, user=None, status_code: int = 400
    ) -> SystemActivity | None:
        if reason not in LoginFailureReason.values:
            reason = LoginFailureReason.INVALID_CREDENTIALS
        extra: dict[str, Any] = {
            "reason": str(reason),
            "user_found": user is not None,
            "identifier_masked": mask_identifier(identifier),
            "note": _FAILURE_NOTES[LoginFailureReason(reason)],
        }
        return cls._auth_event(
            request,
            SystemActivityKind.LOGIN_FAILED,
            user=user,
            status_code=status_code,
            label="محاولة تسجيل دخول فاشلة",
            route=LOGIN_ROUTE,
            extra=extra,
        )

    @classmethod
    def record_logout(cls, request, user) -> SystemActivity | None:
        return cls._auth_event(
            request,
            SystemActivityKind.LOGOUT,
            user=user,
            status_code=200,
            label="تسجيل الخروج",
            route=LOGOUT_ROUTE,
            extra={},
        )
