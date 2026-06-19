from django.core.cache import cache
from django.db import connection
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.permissions import AllowAny
from rest_framework import status
from rest_framework.views import APIView

from apps.common.responses import error_response, success_response


class HealthCheckView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        auth=[],
        responses={200: OpenApiResponse(description="Service health status")},
    )
    def get(self, request):
        return success_response(
            message="OK",
            data={
                "status": "healthy",
                "service": "panorama_backend",
            },
        )


class DatabaseHealthCheckView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        auth=[],
        responses={200: OpenApiResponse(description="Database health status")},
    )
    def get(self, request):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return success_response(
            message="OK",
            data={
                "status": "healthy",
                "service": "panorama_backend",
                "database": "healthy",
            },
        )


class ReadinessCheckView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        auth=[],
        responses={200: OpenApiResponse(description="Service readiness status")},
    )
    def get(self, request):
        checks = {"database": "unknown", "cache": "unknown"}
        ready = True

        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            checks["database"] = "healthy"
        except Exception:
            checks["database"] = "unhealthy"
            ready = False

        try:
            cache_key = "health:ready"
            cache.set(cache_key, "ok", timeout=5)
            checks["cache"] = "healthy" if cache.get(cache_key) == "ok" else "unhealthy"
            ready = ready and checks["cache"] == "healthy"
        except Exception:
            checks["cache"] = "unhealthy"
            ready = False

        data = {
            "status": "ready" if ready else "not_ready",
            "service": "panorama_backend",
            **checks,
        }
        if ready:
            return success_response(message="OK", data=data)
        return error_response(
            message="Service is not ready",
            errors=data,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            request_id=getattr(request, "request_id", None),
        )
