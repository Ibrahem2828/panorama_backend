"""Offline protected packages for library files: license endpoints, encrypted package stream, sync and staff tools.

Same contract as ``apps.lectures.offline_views`` (docs/OFFLINE_FILES.md); a file that is big enough to be packaged by a
worker answers ``202 FILE_OFFLINE_PREPARING`` and the app repeats the request.
"""

from __future__ import annotations

from django.http import Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.accounts.permissions import CanManageFiles
from apps.common.negotiation import IgnoreClientContentNegotiation
from apps.common.offline import no_store, package_response
from apps.common.responses import success_response
from apps.common.throttles import FileOfflineLicenseRateThrottle, FileOfflinePackageRateThrottle
from apps.product.services import feature_enabled_or_raise

from . import offline_services as offline
from .models import FileOfflineLicense, FileResource

MAX_SYNC_IDS = 500
MAX_LIST = 500
FEATURE = "file_offline_enabled"


class FileOfflineSyncSerializer(serializers.Serializer):
    license_ids = serializers.ListField(child=serializers.CharField(max_length=64), max_length=MAX_SYNC_IDS)


class FileOfflineStaffRevokeSerializer(serializers.Serializer):
    user_id = serializers.IntegerField(min_value=1)
    file_id = serializers.IntegerField(min_value=1, required=False)
    note = serializers.CharField(max_length=200, required=False, allow_blank=True)


def _preparing(request):
    return no_store(
        success_response(
            data={"state": "preparing", "retry_after_seconds": 15},
            message="The offline package is being prepared.",
            status_code=status.HTTP_202_ACCEPTED,
            request=request,
            code="FILE_OFFLINE_PREPARING",
        )
    )


class FileOfflineLicenseView(APIView):
    """``POST`` issues or refreshes the license for this device and returns the content key; ``DELETE`` releases it."""

    throttle_classes = [FileOfflineLicenseRateThrottle]

    @extend_schema(
        tags=["File offline"],
        request=None,
        responses={200: OpenApiTypes.OBJECT, 201: OpenApiTypes.OBJECT, 202: OpenApiTypes.OBJECT},
    )
    def post(self, request, pk: int):
        feature_enabled_or_raise(FEATURE, request=request)
        try:
            license_, created = offline.issue_license(
                request.user, pk, offline.device_hash_from_request(request), request=request
            )
        except offline.OfflinePackagePreparing:
            return _preparing(request)
        return no_store(
            success_response(
                data=offline.license_payload(license_),
                status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
                request=request,
                code="FILE_OFFLINE_LICENSE_ISSUED" if created else "FILE_OFFLINE_LICENSE_REFRESHED",
            )
        )

    @extend_schema(tags=["File offline"], responses={200: OpenApiTypes.OBJECT})
    def delete(self, request, pk: int):
        released = offline.release_license(request.user, pk, offline.device_hash_from_request(request), request=request)
        return success_response(
            data={"released": released},
            message="Offline license released.",
            request=request,
            code="FILE_OFFLINE_RELEASED",
        )


class FileOfflineLicenseRenewView(APIView):
    throttle_classes = [FileOfflineLicenseRateThrottle]

    @extend_schema(tags=["File offline"], request=None, responses={200: OpenApiTypes.OBJECT})
    def post(self, request, pk: int):
        feature_enabled_or_raise(FEATURE, request=request)
        license_ = offline.renew_license(request.user, pk, offline.device_hash_from_request(request), request=request)
        return no_store(
            success_response(
                data=offline.license_payload(license_), request=request, code="FILE_OFFLINE_LICENSE_RENEWED"
            )
        )


class FileOfflinePackageView(APIView):
    """Streams the AES-GCM *ciphertext* package. Plaintext never leaves the server on this route."""

    throttle_classes = [FileOfflinePackageRateThrottle]
    content_negotiation_class = IgnoreClientContentNegotiation

    @extend_schema(
        tags=["File offline"],
        responses={200: OpenApiResponse(description="Encrypted package"), 206: OpenApiResponse(description="Range")},
    )
    def get(self, request, pk: int):
        feature_enabled_or_raise(FEATURE, request=request)
        license_ = offline.get_license(request.user, pk, offline.device_hash_from_request(request))
        return package_response(request, license_.blob, f"file-{pk}.pnof")


class FileOfflineLicenseListView(APIView):
    """The caller's licenses on this device (state included) so the app can see what must be purged."""

    throttle_classes = [FileOfflineLicenseRateThrottle]

    @extend_schema(tags=["File offline"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        queryset = FileOfflineLicense.objects.select_related("blob").filter(
            user=request.user, device_hash=offline.device_hash_from_request(request)
        )[:MAX_LIST]
        return no_store(
            success_response(
                data=[offline.license_summary(item) for item in queryset],
                request=request,
                code="FILE_OFFLINE_LICENSES",
            )
        )


class FileOfflineSyncView(APIView):
    throttle_classes = [FileOfflineLicenseRateThrottle]

    @extend_schema(tags=["File offline"], request=FileOfflineSyncSerializer, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = FileOfflineSyncSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = offline.sync_inventory(
            request.user, offline.device_hash_from_request(request), list(serializer.validated_data["license_ids"])
        )
        return no_store(success_response(data=result, request=request, code="FILE_OFFLINE_SYNC"))


class DashboardFileOfflineLicenseListView(APIView):
    """Staff view of one student's (or one file's) offline licenses. Capability: ``files.manage``."""

    permission_classes = [CanManageFiles]

    @extend_schema(tags=["Dashboard"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        user_id, file_id = request.query_params.get("user"), request.query_params.get("file")
        if not user_id and not file_id:
            raise ValidationError({"user": "Filter by user or file."})
        queryset = FileOfflineLicense.objects.select_related("blob", "user", "file_resource")
        try:
            if user_id:
                queryset = queryset.filter(user_id=int(user_id))
            if file_id:
                queryset = queryset.filter(file_resource_id=int(file_id))
        except ValueError as exc:
            raise ValidationError({"user": "Integer ids are required."}) from exc
        rows = [
            {
                **offline.license_summary(item),
                "user_id": item.user_id,
                "file_title": item.file_resource.title,
                "device": item.device_hash[:8],
                "renew_count": item.renew_count,
            }
            for item in queryset[:200]
        ]
        return success_response(data=rows, request=request, code="DASHBOARD_FILE_OFFLINE_LICENSES")


class DashboardFileOfflineLicenseRevokeView(APIView):
    permission_classes = [CanManageFiles]

    @extend_schema(tags=["Dashboard"], request=FileOfflineStaffRevokeSerializer, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = FileOfflineStaffRevokeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user = get_object_or_404(User, pk=data["user_id"])
        resource = None
        if "file_id" in data:
            resource = FileResource.objects.filter(pk=data["file_id"]).first()
            if resource is None:
                raise Http404("File not found.")
        revoked = offline.revoke_user_licenses(
            user, offline.REASON_STAFF, file_resource=resource, actor=request.user, request=request
        )
        return success_response(
            data={"revoked": revoked},
            message="Offline licenses revoked.",
            request=request,
            code="FILE_OFFLINE_REVOKED",
        )
