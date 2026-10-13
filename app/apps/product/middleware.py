from __future__ import annotations

import logging

from django.core.cache import cache
from django.db import DatabaseError
from django.http import JsonResponse
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.authentication import SESSION_VERSION_CLAIM
from apps.accounts.models import User

from .services import ProductConfigurationService

lifecycle_logger = logging.getLogger("panorama.product.lifecycle")

_ROLE_CACHE_SECONDS = 30


class ProductLifecycleMiddleware:
    """Apply server-controlled maintenance and minimum-version policy to API traffic.

    The middleware only acts when a recognized mobile platform header is sent. Browser
    and dashboard clients therefore keep their stable v1 contract during migration.

    Lifecycle lookups fail open. Middleware runs outside DRF, and Django does not route
    middleware exceptions through ``process_exception``, so an unhandled database error
    here bypasses the API error envelope and returns raw HTML 500 for every non-exempt
    request. Serving traffic without a maintenance banner is strictly safer than losing
    the whole API surface, and readiness still fails closed on unapplied migrations.
    """

    _public_prefixes = (
        "/api/v1/health/",
        "/api/v1/mobile/bootstrap/",
        "/api/v1/mobile/update-policy/",
        "/api/v1/policies/current/",
        # Staff must still be able to sign in during maintenance to switch it off from the dashboard. Both web
        # apps read /auth/me/ right after login to build the session, so it has to stay reachable too.
        "/api/v1/auth/login/",
        "/api/v1/auth/token/refresh/",
        "/api/v1/auth/me/",
    )
    _dashboard_prefix = "/api/v1/dashboard/"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if not path.startswith("/api/v1/") or path.startswith(self._public_prefixes):
            return self.get_response(request)
        if path.startswith(self._dashboard_prefix):
            # Dashboard endpoints remain protected by their own capability checks so a
            # super-admin can always disable maintenance or correct a release policy.
            return self.get_response(request)

        maintenance = self._lifecycle_lookup(request, "maintenance", ProductConfigurationService.active_maintenance)
        if maintenance and self._bypasses_maintenance(request, maintenance):
            maintenance = None  # allowed role: serve normally (the release gate below still applies)
        if maintenance:
            response = self._error(
                request,
                status=503,
                code="MAINTENANCE_MODE",
                message=maintenance.message_en or "Service is temporarily unavailable for maintenance.",
                details={
                    "title_ar": maintenance.title_ar,
                    "title_en": maintenance.title_en,
                    "message_ar": maintenance.message_ar,
                    "estimated_end_at": maintenance.estimated_end_at.isoformat()
                    if maintenance.estimated_end_at
                    else None,
                },
            )
            response["Retry-After"] = str(maintenance.retry_after_seconds)
            return response

        platform = request.headers.get("X-App-Platform", "").strip().lower()
        if platform not in {"android", "ios"}:
            return self.get_response(request)
        build = self._safe_int(request.headers.get("X-App-Build", "0"))
        policy = self._lifecycle_lookup(
            request, "release_policy", ProductConfigurationService.active_release_policy, platform
        )
        if policy and policy.requires_update_for(build):
            return self._error(
                request,
                status=426,
                code="APP_UPDATE_REQUIRED",
                message=policy.message_en or "A newer version of the application is required.",
                details={
                    "minimum_supported_build": policy.minimum_supported_build,
                    "latest_build": policy.latest_build,
                    "latest_version": policy.latest_version,
                    "store_url": policy.store_url,
                    "message_ar": policy.message_ar,
                    "message_en": policy.message_en,
                },
            )
        return self.get_response(request)

    @classmethod
    def _bypasses_maintenance(cls, request, maintenance) -> bool:
        """Whether the caller's role may use the API while ``maintenance`` is active.

        Allowed when the role is listed in ``allowed_roles``, or when ``bypass_staff`` is set and the role is a staff
        role (any role that holds dashboard capabilities, i.e. everything except student and normal_user). The role
        comes from the verified access token's user id; anonymous, expired, malformed or revoked tokens never bypass.
        """

        from apps.accounts.permissions import ROLE_CAPABILITIES

        allowed_roles = {str(role) for role in (maintenance.allowed_roles or [])}
        staff_roles = {role for role, capabilities in ROLE_CAPABILITIES.items() if capabilities}
        if not allowed_roles and not maintenance.bypass_staff:
            return False  # nobody can bypass: skip token work entirely
        role = cls._token_role(request)
        if role is None:
            return False
        return role in allowed_roles or (bool(maintenance.bypass_staff) and role in staff_roles)

    @classmethod
    def _token_role(cls, request) -> str | None:
        """Role of the bearer-token user, or None. Cached ``_ROLE_CACHE_SECONDS`` per user so only the first call in
        a window touches the database, and only while maintenance is active. A DB error means "no bypass"."""

        header = request.headers.get("Authorization", "")
        scheme, _, raw_token = header.partition(" ")
        if scheme.lower() != "bearer" or not raw_token.strip():
            return None
        try:
            token = AccessToken(raw_token.strip())  # verifies signature, expiry and token type
            user_id = token[api_settings.USER_ID_CLAIM]
            token_version = int(token.get(SESSION_VERSION_CLAIM, 1))
        except (TokenError, KeyError, TypeError, ValueError):
            return None
        cache_key = f"panorama:product:maintenance-role:v1:{user_id}"
        try:
            state = cache.get(cache_key)
        except Exception:  # noqa: BLE001 - a cache outage must not break the request
            state = None
        if state is None:
            try:
                row = (
                    User.objects.filter(pk=user_id, is_active=True, is_deleted=False)
                    .values("role", "session_version")
                    .first()
                )
            except DatabaseError:
                return None
            state = {"role": row["role"], "sv": row["session_version"]} if row else {}
            try:
                cache.set(cache_key, state, _ROLE_CACHE_SECONDS)
            except Exception:  # noqa: BLE001
                pass
        if not state or state["sv"] != token_version:
            return None  # unknown/inactive user or a token revoked by a role/status/password change
        return str(state["role"])

    @staticmethod
    def _lifecycle_lookup(request, lookup: str, resolver, *args):
        try:
            return resolver(*args)
        except DatabaseError as exc:
            lifecycle_logger.warning(
                "product_lifecycle_lookup_failed",
                extra={
                    "request_id": getattr(request, "request_id", None),
                    "lookup": lookup,
                    "failure_class": type(exc).__name__,
                },
            )
            return None

    @staticmethod
    def _safe_int(value: str) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _error(request, *, status: int, code: str, message: str, details: dict) -> JsonResponse:
        payload = {
            "success": False,
            "code": code,
            "message": message,
            "errors": details,
        }
        request_id = getattr(request, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        return JsonResponse(payload, status=status)
