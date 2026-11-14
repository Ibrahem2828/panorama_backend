"""Offline protected lecture packages: license endpoints, encrypted package stream, sync and staff tools."""

from __future__ import annotations

from django.http import FileResponse, Http404, HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.accounts.permissions import CanManageLectures
from apps.common.negotiation import IgnoreClientContentNegotiation
from apps.common.responses import success_response
from apps.common.throttles import LectureOfflineLicenseRateThrottle, LectureOfflinePackageRateThrottle
from apps.files.views import _parse_single_range, _range_chunks
from apps.product.services import feature_enabled_or_raise

from . import offline_services as offline
from .models import Lecture, LectureOfflineLicense

MAX_SYNC_IDS = 500
MAX_LIST = 500


class OfflineSyncSerializer(serializers.Serializer):
    license_ids = serializers.ListField(child=serializers.CharField(max_length=64), max_length=MAX_SYNC_IDS)


class StaffRevokeSerializer(serializers.Serializer):
    user_id = serializers.IntegerField(min_value=1)
    lecture_id = serializers.IntegerField(min_value=1, required=False)
    note = serializers.CharField(max_length=200, required=False, allow_blank=True)


def _no_store(response):
    response["Cache-Control"] = "private, no-store, max-age=0"
    response["Pragma"] = "no-cache"
    response["X-Content-Type-Options"] = "nosniff"
    return response


class LectureOfflineLicenseView(APIView):
    """``POST`` issues or refreshes the license for this device and returns the content key; ``DELETE`` releases it."""

    throttle_classes = [LectureOfflineLicenseRateThrottle]

    @extend_schema(
        tags=["Lecture offline"], request=None, responses={200: OpenApiTypes.OBJECT, 201: OpenApiTypes.OBJECT}
    )
    def post(self, request, pk: int):
        feature_enabled_or_raise("lecture_offline_enabled", request=request)
        license_, created = offline.issue_license(
            request.user, pk, offline.device_hash_from_request(request), request=request
        )
        response = success_response(
            data=offline.license_payload(license_),
            status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
            request=request,
            code="LECTURE_OFFLINE_LICENSE_ISSUED" if created else "LECTURE_OFFLINE_LICENSE_REFRESHED",
        )
        return _no_store(response)

    @extend_schema(tags=["Lecture offline"], responses={200: OpenApiTypes.OBJECT})
    def delete(self, request, pk: int):
        released = offline.release_license(request.user, pk, offline.device_hash_from_request(request), request=request)
        return success_response(
            data={"released": released},
            message="Offline license released.",
            request=request,
            code="LECTURE_OFFLINE_RELEASED",
        )


class LectureOfflineLicenseRenewView(APIView):
    throttle_classes = [LectureOfflineLicenseRateThrottle]

    @extend_schema(tags=["Lecture offline"], request=None, responses={200: OpenApiTypes.OBJECT})
    def post(self, request, pk: int):
        feature_enabled_or_raise("lecture_offline_enabled", request=request)
        license_ = offline.renew_license(request.user, pk, offline.device_hash_from_request(request), request=request)
        return _no_store(
            success_response(
                data=offline.license_payload(license_), request=request, code="LECTURE_OFFLINE_LICENSE_RENEWED"
            )
        )


class LectureOfflinePackageView(APIView):
    """Streams the AES-GCM *ciphertext* package. Plaintext never leaves the server on this route."""

    throttle_classes = [LectureOfflinePackageRateThrottle]
    content_negotiation_class = IgnoreClientContentNegotiation

    @extend_schema(
        tags=["Lecture offline"],
        responses={200: OpenApiResponse(description="Encrypted package"), 206: OpenApiResponse(description="Range")},
    )
    def get(self, request, pk: int):
        feature_enabled_or_raise("lecture_offline_enabled", request=request)
        license_ = offline.get_license(request.user, pk, offline.device_hash_from_request(request))
        blob = license_.blob
        etag = '"' + blob.package_sha256 + '"'
        size = blob.package_size
        if request.headers.get("If-None-Match", "") == etag:
            return _no_store(HttpResponse(status=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag}))
        range_header = request.headers.get("Range", "")
        if_range = request.headers.get("If-Range", "")
        if if_range and if_range != etag:
            range_header = ""  # the validator no longer matches: send the whole (new) representation
        try:
            byte_range = _parse_single_range(range_header, size)
        except ValueError:
            unsatisfiable = HttpResponse(status=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE)
            unsatisfiable["Content-Range"] = f"bytes */{size}"
            return _no_store(unsatisfiable)
        handle = blob.package.open("rb")
        response: HttpResponse | FileResponse | StreamingHttpResponse
        if byte_range is None:
            response = FileResponse(handle, content_type="application/octet-stream")
            response["Content-Length"] = str(size)
        else:
            start, end = byte_range
            handle.seek(start)
            response = StreamingHttpResponse(
                _range_chunks(handle, end - start + 1), status=206, content_type="application/octet-stream"
            )
            response["Content-Range"] = f"bytes {start}-{end}/{size}"
            response["Content-Length"] = str(end - start + 1)
        response["Accept-Ranges"] = "bytes"
        response["ETag"] = etag
        response["Content-Disposition"] = f'attachment; filename="lecture-{pk}.pnof"'
        response["X-Package-Key-Id"] = str(blob.key_id)
        return _no_store(response)


class LectureOfflineLicenseListView(APIView):
    """The caller's licenses on this device (state included) so the app can see what must be purged."""

    throttle_classes = [LectureOfflineLicenseRateThrottle]

    @extend_schema(tags=["Lecture offline"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        queryset = LectureOfflineLicense.objects.select_related("blob").filter(
            user=request.user, device_hash=offline.device_hash_from_request(request)
        )[:MAX_LIST]
        return _no_store(
            success_response(
                data=[offline.license_summary(item) for item in queryset],
                request=request,
                code="LECTURE_OFFLINE_LICENSES",
            )
        )


class LectureOfflineSyncView(APIView):
    throttle_classes = [LectureOfflineLicenseRateThrottle]

    @extend_schema(tags=["Lecture offline"], request=OfflineSyncSerializer, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = OfflineSyncSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = offline.sync_inventory(
            request.user, offline.device_hash_from_request(request), list(serializer.validated_data["license_ids"])
        )
        return _no_store(success_response(data=result, request=request, code="LECTURE_OFFLINE_SYNC"))


class DashboardOfflineLicenseListView(APIView):
    """Staff view of one student's (or one lecture's) offline licenses. Capability: ``lectures.manage``."""

    permission_classes = [CanManageLectures]

    @extend_schema(tags=["Dashboard lectures"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        user_id, lecture_id = request.query_params.get("user"), request.query_params.get("lecture")
        if not user_id and not lecture_id:
            raise ValidationError({"user": "Filter by user or lecture."})
        queryset = LectureOfflineLicense.objects.select_related("blob", "user", "lecture")
        try:
            if user_id:
                queryset = queryset.filter(user_id=int(user_id))
            if lecture_id:
                queryset = queryset.filter(lecture_id=int(lecture_id))
        except ValueError as exc:
            raise ValidationError({"user": "Integer ids are required."}) from exc
        rows = [
            {
                **offline.license_summary(item),
                "user_id": item.user_id,
                "lecture_title": item.lecture.title,
                "device": item.device_hash[:8],
                "renew_count": item.renew_count,
            }
            for item in queryset[:200]
        ]
        return success_response(data=rows, request=request, code="DASHBOARD_LECTURE_OFFLINE_LICENSES")


class DashboardOfflineLicenseRevokeView(APIView):
    permission_classes = [CanManageLectures]

    @extend_schema(tags=["Dashboard lectures"], request=StaffRevokeSerializer, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = StaffRevokeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user = get_object_or_404(User, pk=data["user_id"])
        lecture = None
        if "lecture_id" in data:
            lecture = Lecture.objects.filter(pk=data["lecture_id"]).first()
            if lecture is None:
                raise Http404("Lecture not found.")
        revoked = offline.revoke_user_licenses(
            user, offline.REASON_STAFF, lecture=lecture, actor=request.user, request=request
        )
        return success_response(
            data={"revoked": revoked},
            message="Offline licenses revoked.",
            request=request,
            code="LECTURE_OFFLINE_REVOKED",
        )
