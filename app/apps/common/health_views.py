import logging
import os

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from apps.common.responses import error_response, success_response

health_logger = logging.getLogger(__name__)


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


class LivenessHealthCheckView(APIView):
    """Process liveness only: it deliberately avoids every external dependency."""

    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(auth=[], responses={200: OpenApiResponse(description="Process is alive")})
    def get(self, request):
        return success_response(
            message="Live",
            data={"status": "live", "service": "panorama_backend"},
            request=request,
            code="LIVE",
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


class ReadinessHealthCheckView(APIView):
    """Readiness is intentionally stricter than liveness."""

    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        auth=[],
        responses={
            200: OpenApiResponse(description="Service dependencies are ready"),
            503: OpenApiResponse(description="A required service dependency is unavailable"),
        },
    )
    def get(self, request):
        checks = _dependency_checks()
        if checks is None:
            return _not_ready(request)
        return success_response(
            message="Ready",
            data={"status": "ready", "service": "panorama_backend", **checks},
            request=request,
            code="READY",
        )


class StartupHealthCheckView(APIView):
    """Verify startup prerequisites, including migration state, before traffic is admitted."""

    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        auth=[],
        responses={200: OpenApiResponse(description="Startup checks passed"), 503: OpenApiResponse(description="Startup checks failed")},
    )
    def get(self, request):
        checks = _dependency_checks(check_migrations=True, check_configuration=True)
        if checks is None:
            return _not_ready(request, code="STARTUP_NOT_READY")
        return success_response(
            message="Startup checks passed",
            data={"status": "started", "service": "panorama_backend", **checks},
            request=request,
            code="STARTUP_READY",
        )


def _dependency_checks(*, check_migrations: bool = True, check_configuration: bool = True):
    """Return safe dependency state, or None without exposing dependency details."""

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        cache.get("panorama:readiness")
        checks = {"database": "healthy", "cache": "healthy"}
        if check_migrations:
            executor = MigrationExecutor(connection)
            if executor.migration_plan(executor.loader.graph.leaf_nodes()):
                health_logger.warning("health_pending_migrations")
                return None
            checks["migrations"] = "current"
        if check_configuration and not _critical_configuration_is_valid():
            health_logger.warning("health_invalid_critical_configuration")
            return None
        if check_configuration:
            checks["configuration"] = "valid"
        return checks
    except Exception as exc:  # Deliberately do not expose database/cache internals.
        health_logger.warning("health_dependency_check_failed:%s", type(exc).__name__)
        return None


def _critical_configuration_is_valid() -> bool:
    """Production settings validate most values at import; check storage configuration too."""

    if os.environ.get("DJANGO_SETTINGS_MODULE") != "config.settings.production":
        return True
    storage = getattr(settings, "STORAGES", {}).get("default", {})
    return bool(
        getattr(settings, "USE_S3_STORAGE", False)
        and storage.get("BACKEND") == "storages.backends.s3.S3Storage"
        and getattr(settings, "FIELD_ENCRYPTION_KEY", "")
        and getattr(settings, "AWS_STORAGE_BUCKET_NAME", "")
        and getattr(settings, "S3_BUCKET_PRIVATE", False)
    )


def _not_ready(request, *, code: str = "SERVICE_NOT_READY"):
    return error_response(
        message="Service dependencies are unavailable",
        status_code=503,
        request=request,
        code=code,
    )
