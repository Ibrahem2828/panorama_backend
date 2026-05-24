from django.db import connection
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from apps.common.responses import success_response


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
