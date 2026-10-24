from __future__ import annotations

import mimetypes
from datetime import timedelta
from pathlib import Path
from typing import cast

from django.db import transaction
from django.db.models import Count
from django.http import FileResponse, Http404, HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.http import content_disposition_header
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework import filters, permissions, status
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.accounts.permissions import CanManageFiles
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.negotiation import IgnoreClientContentNegotiation
from apps.common.responses import success_response
from apps.common.throttles import FileTicketRateThrottle, LectureViewerRateThrottle
from apps.common.viewsets import StandardModelViewSet, StandardReadOnlyModelViewSet
from apps.groups.models import GroupMembershipStatus
from apps.lectures.views import _inline_file_response, _viewer_token
from apps.product.services import feature_enabled_or_raise

from .models import (
    IN_FLIGHT_FILE_STATUSES,
    FileAccessPurpose,
    FileAccessTicket,
    FileProcessingStatus,
    FileResource,
)
from .serializers import FileResourceSerializer
from .services import (
    FileAccessService,
    accessible_files_for_user,
    get_authorized_file_page,
    issue_file_viewer_session,
    user_can_access_file,
)
from .tasks import dispatch_file_processing

FILE_RANGE_CHUNK_SIZE = 64 * 1024


def _parse_single_range(range_header: str, file_size: int) -> tuple[int, int] | None:
    """Parse one RFC 7233 byte range without accepting multi-range responses."""

    if not range_header:
        return None
    if not range_header.startswith("bytes=") or "," in range_header:
        raise ValueError("Unsupported range")
    start_text, separator, end_text = range_header.removeprefix("bytes=").partition("-")
    if not separator or (not start_text and not end_text):
        raise ValueError("Invalid range")
    try:
        if start_text:
            start = int(start_text)
            end = int(end_text) if end_text else file_size - 1
        else:
            suffix_length = int(end_text)
            if suffix_length <= 0:
                raise ValueError("Invalid suffix range")
            start = max(file_size - suffix_length, 0)
            end = file_size - 1
    except ValueError as exc:
        raise ValueError("Invalid range") from exc
    if start < 0 or start >= file_size or end < start:
        raise ValueError("Unsatisfiable range")
    return start, min(end, file_size - 1)


def _range_chunks(file_handle, length: int):
    try:
        remaining = length
        while remaining:
            chunk = file_handle.read(min(FILE_RANGE_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        file_handle.close()


class FileResourceViewSet(StandardReadOnlyModelViewSet):
    serializer_class = FileResourceSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = [
        "visibility",
        "university",
        "faculty",
        "major",
        "academic_year",
        "semester",
        "subject",
        "group",
        "is_active",
        "is_printable",
    ]
    search_fields = ["title", "description"]
    ordering_fields = ["created_at", "title", "file_size"]
    ordering = ["-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return FileResource.objects.none()
        return accessible_files_for_user(self.request.user)

    def get_object(self):
        obj = super().get_object()
        if not user_can_access_file(self.request.user, obj):
            raise PermissionDenied("You do not have access to this file.")
        return obj


class GroupFileResourceViewSet(FileResourceViewSet):
    @extend_schema(tags=["Files"])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return FileResource.objects.none()
        group_id = self.kwargs["group_pk"]
        user = cast(User, self.request.user)
        if not user.group_memberships.filter(
            group_id=group_id,
            status=GroupMembershipStatus.APPROVED,
            is_deleted=False,
        ).exists():
            raise PermissionDenied("You are not an approved member of this group.")
        return accessible_files_for_user(user).filter(group_id=group_id)


class DashboardFileResourceViewSet(StandardModelViewSet):
    permission_classes = [CanManageFiles]
    serializer_class = FileResourceSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = FileResourceViewSet.filterset_fields
    search_fields = ["title", "description", "sha256"]
    ordering_fields = ["created_at", "title", "file_size", "pages_count"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return FileResource.objects.filter(is_deleted=False).select_related(
            "uploaded_by", "university", "faculty", "major", "academic_year", "semester", "subject", "group"
        )

    def perform_update(self, serializer):
        resource = serializer.save()
        if resource.processing_status == FileProcessingStatus.QUEUED:  # a new document was uploaded
            dispatch_file_processing(resource.pk)

    @extend_schema(tags=["Dashboard"], request=None, responses={202: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["post"], url_path="reprocess")
    def reprocess(self, request, pk=None):
        """Re-queue page rendering for one file (failed, never processed, or stalled)."""

        resource = get_object_or_404(FileResource, pk=pk, is_deleted=False)
        _queue_reprocess(resource, request)
        return success_response(
            data={"id": resource.pk, "processing_status": FileProcessingStatus.QUEUED},
            message="File processing re-queued.",
            status_code=status.HTTP_202_ACCEPTED,
            request=request,
            code="FILE_PROCESSING_REQUEUED",
        )

    @extend_schema(tags=["Dashboard"], responses={200: OpenApiTypes.OBJECT})
    @action(detail=False, methods=["get"], url_path="processing-summary")
    def processing_summary(self, request):
        counts = {value: 0 for value in FileProcessingStatus.values}
        for row in FileResource.objects.filter(is_deleted=False).values("processing_status").annotate(n=Count("id")):
            counts[row["processing_status"]] = row["n"]
        return success_response(data=counts, message="File processing summary", request=request)

    @extend_schema(tags=["Dashboard"], request=OpenApiTypes.OBJECT, responses={202: OpenApiTypes.OBJECT})
    @action(detail=False, methods=["post"], url_path="reprocess-bulk")
    def reprocess_bulk(self, request):
        """Re-queue many files: ids or a scope (unprocessed / failed). Never touches ready or quarantined files."""

        ids = request.data.get("ids")
        scope = request.data.get("scope")
        queryset = FileResource.objects.filter(is_deleted=False, is_active=True)
        if isinstance(ids, list) and ids:
            queryset = queryset.filter(pk__in=[int(value) for value in ids if str(value).isdigit()][:500])
        elif scope == "failed":
            queryset = queryset.filter(processing_status=FileProcessingStatus.FAILED)
        elif scope == "unprocessed":
            queryset = queryset.filter(processing_status=FileProcessingStatus.PENDING)
        else:
            raise ValidationError({"detail": "Send ids or a scope (unprocessed or failed)."})
        queued = 0
        skipped = (FileProcessingStatus.READY, FileProcessingStatus.QUARANTINED, *IN_FLIGHT_FILE_STATUSES)
        for resource in queryset.exclude(processing_status__in=skipped)[:500]:
            _queue_reprocess(resource, request, audit=False)
            queued += 1
        return success_response(
            data={"queued": queued}, message="Files re-queued", status_code=status.HTTP_202_ACCEPTED, request=request
        )

    def perform_create(self, serializer):
        resource = serializer.save(uploaded_by=self.request.user)
        dispatch_file_processing(resource.pk)
        AuditLogService.log(
            actor=self.request.user,
            action=AuditAction.FILE_UPLOADED,
            target=resource,
            new_value={"visibility": resource.visibility, "sha256": resource.sha256},
            request=self.request,
        )


class FileNotReprocessable(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "This file is being processed or cannot be reprocessed."
    default_code = "file_not_reprocessable"


def _queue_reprocess(resource: FileResource, request, *, audit: bool = True) -> None:
    with transaction.atomic():
        locked = FileResource.objects.select_for_update().get(pk=resource.pk)
        recently_active = locked.updated_at > timezone.now() - timedelta(minutes=30)
        if locked.processing_status in IN_FLIGHT_FILE_STATUSES and recently_active:
            raise FileNotReprocessable()
        if locked.processing_status == FileProcessingStatus.QUARANTINED:
            raise FileNotReprocessable()
        previous = locked.processing_status
        locked.processing_status = FileProcessingStatus.QUEUED
        locked.failure_code = ""
        locked.failure_message = ""
        locked.processing_task_id = ""
        locked.save(
            update_fields=["processing_status", "failure_code", "failure_message", "processing_task_id", "updated_at"]
        )
        if audit:
            AuditLogService.log(
                actor=request.user,
                action=AuditAction.FILE_UPLOADED,
                target=locked,
                old_value={"processing_status": previous},
                new_value={"processing_status": locked.processing_status, "reprocess": True},
                request=request,
            )
        dispatch_file_processing(locked.pk)


class FileAccessTicketView(APIView):
    throttle_classes = [FileTicketRateThrottle]

    @extend_schema(tags=["Files"], request=None, responses={201: OpenApiTypes.OBJECT})
    def post(self, request, pk: int):
        file_resource = get_object_or_404(FileResource, pk=pk, is_active=True, is_deleted=False)
        purpose = request.data.get("purpose", FileAccessPurpose.VIEW)
        if purpose not in FileAccessPurpose.values:
            purpose = FileAccessPurpose.VIEW
        ticket = FileAccessService.issue_ticket(request.user, file_resource, request, purpose=purpose)
        preview_url = request.build_absolute_uri(f"/api/v1/protected-files/{ticket.token}/")
        AuditLogService.log(
            actor=request.user,
            action=AuditAction.FILE_ACCESS_TICKET_ISSUED,
            target=file_resource,
            new_value={"ticket_id": ticket.id, "purpose": purpose, "expires_at": ticket.expires_at.isoformat()},
            request=request,
        )
        return success_response(
            data={"preview_url": preview_url, "expires_at": ticket.expires_at, "download_allowed": False},
            message="Protected file access ticket issued",
            status_code=status.HTTP_201_CREATED,
            request=request,
            code="FILE_ACCESS_TICKET_ISSUED",
        )


class ProtectedFileStreamView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        tags=["Protected Assets"],
        parameters=[
            OpenApiParameter(
                name="Range",
                type=str,
                location=OpenApiParameter.HEADER,
                required=False,
                description="Optional single RFC 7233 byte range for PDF/WebView streaming.",
            )
        ],
        responses={
            200: OpenApiResponse(description="Inline protected file stream."),
            206: OpenApiResponse(description="Requested byte range of an inline protected file stream."),
            416: OpenApiResponse(description="The requested byte range is invalid or unsatisfiable."),
        },
    )
    def get(self, request, token):
        with transaction.atomic():
            ticket = (
                FileAccessTicket.objects.select_for_update()
                .select_related("file_resource", "user")
                .filter(token=token, is_deleted=False)
                .first()
            )
            if not ticket or not ticket.is_valid or ticket.user_id != request.user.id:
                raise Http404("The protected file link is invalid or expired.")
            resource = ticket.file_resource
            if not resource.file or not user_can_access_file(request.user, resource):
                raise Http404("File is unavailable.")
            ticket.use_count += 1
            ticket.save(update_fields=["use_count", "updated_at"])
        filename = Path(resource.file.name).name
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        file_size = resource.file.size
        response: HttpResponse | FileResponse | StreamingHttpResponse
        try:
            byte_range = _parse_single_range(request.headers.get("Range", ""), file_size)
        except ValueError:
            response = HttpResponse(status=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE)
            response["Content-Range"] = f"bytes */{file_size}"
            return response

        if byte_range is None:
            response = FileResponse(resource.file.open("rb"), content_type=content_type)
        else:
            start, end = byte_range
            file_handle = resource.file.open("rb")
            file_handle.seek(start)
            response = StreamingHttpResponse(
                _range_chunks(file_handle, end - start + 1), status=206, content_type=content_type
            )
            response["Content-Range"] = f"bytes {start}-{end}/{file_size}"
            response["Content-Length"] = str(end - start + 1)
        response["Accept-Ranges"] = "bytes"
        content_disposition = content_disposition_header(False, filename)
        if content_disposition:
            response["Content-Disposition"] = content_disposition
        response["Cache-Control"] = "private, no-store, max-age=0"
        response["Pragma"] = "no-cache"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = (
            "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"
        )
        return response


# -- protected page viewer (same engine and wire format as the lecture viewer) ---------------------------------------------


def _viewable_file(request, pk: int) -> FileResource:
    resource = get_object_or_404(accessible_files_for_user(request.user), pk=pk)
    if not user_can_access_file(request.user, resource):
        raise NotFound("File is unavailable.")
    return resource


class FileViewerManifestView(APIView):
    throttle_classes = [LectureViewerRateThrottle]

    @extend_schema(tags=["File viewer"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request, pk: int):
        feature_enabled_or_raise("lecture_viewer_enabled", request=request)
        resource = _viewable_file(request, pk)
        return success_response(
            data={
                "file_id": resource.pk,
                "title": resource.title,
                "status": resource.processing_status,
                "page_count": resource.pages_count or 0,
                "ready": resource.viewer_ready,
                "text_available": resource.pages.filter(text_content__gt="").exists(),
                "viewer_capabilities": {"page_streaming": True, "download_original": False, "annotations": True},
            },
            request=request,
            code="FILE_VIEWER_MANIFEST",
        )


class FileViewerSessionView(APIView):
    throttle_classes = [LectureViewerRateThrottle]

    @extend_schema(tags=["File viewer"], request=None, responses={201: OpenApiTypes.OBJECT})
    def post(self, request, pk: int):
        feature_enabled_or_raise("lecture_viewer_enabled", request=request)
        resource = _viewable_file(request, pk)
        session = issue_file_viewer_session(request.user, resource)
        return success_response(
            data={"session_token": str(session.token), "expires_at": session.expires_at},
            status_code=status.HTTP_201_CREATED,
            request=request,
            code="FILE_VIEWER_SESSION_CREATED",
        )


class FileViewerPageView(APIView):
    throttle_classes = [LectureViewerRateThrottle]
    content_negotiation_class = IgnoreClientContentNegotiation

    @extend_schema(tags=["File viewer"], responses={200: OpenApiResponse(description="Protected page image")})
    def get(self, request, pk: int, page_number: int):
        feature_enabled_or_raise("lecture_viewer_enabled", request=request)
        resource = _viewable_file(request, pk)
        page = get_authorized_file_page(request.user, resource, _viewer_token(request), page_number)
        return _inline_file_response(page.rendered_file, f"file-{resource.pk}-page-{page_number}.png", "image/png")


class FileViewerThumbnailView(APIView):
    throttle_classes = [LectureViewerRateThrottle]
    content_negotiation_class = IgnoreClientContentNegotiation

    @extend_schema(tags=["File viewer"], responses={200: OpenApiResponse(description="Protected thumbnail image")})
    def get(self, request, pk: int, page_number: int):
        feature_enabled_or_raise("lecture_viewer_enabled", request=request)
        resource = _viewable_file(request, pk)
        page = get_authorized_file_page(request.user, resource, _viewer_token(request), page_number)
        if not page.thumbnail:
            raise Http404("Thumbnail is unavailable.")
        return _inline_file_response(page.thumbnail, f"file-{resource.pk}-thumbnail-{page_number}.png", "image/png")


class FileViewerTextView(APIView):
    throttle_classes = [LectureViewerRateThrottle]

    @extend_schema(tags=["File viewer"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request, pk: int, page_number: int):
        feature_enabled_or_raise("lecture_viewer_enabled", request=request)
        resource = _viewable_file(request, pk)
        page = get_authorized_file_page(request.user, resource, _viewer_token(request), page_number)
        return success_response(
            data={
                "lecture_id": resource.pk,
                "file_id": resource.pk,
                "page_number": page_number,
                "text": page.text_content,
            },
            request=request,
            code="FILE_PAGE_TEXT",
        )
