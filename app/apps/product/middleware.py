from __future__ import annotations

from django.http import JsonResponse

from .services import ProductConfigurationService


class ProductLifecycleMiddleware:
    """Apply server-controlled maintenance and minimum-version policy to API traffic.

    The middleware only acts when a recognized mobile platform header is sent. Browser
    and dashboard clients therefore keep their stable v1 contract during migration.
    """

    _public_prefixes = (
        "/api/v1/health/",
        "/api/v1/mobile/bootstrap/",
        "/api/v1/mobile/update-policy/",
        "/api/v1/policies/current/",
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

        maintenance = ProductConfigurationService.active_maintenance()
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
        policy = ProductConfigurationService.active_release_policy(platform)
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
