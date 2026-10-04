from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import cast

from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils.http import content_disposition_header
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework import filters, permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.accounts.permissions import CanManageFiles
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.responses import success_response
from apps.common.throttles import FileTicketRateThrottle
from apps.common.viewsets import StandardModelViewSet, StandardReadOnlyModelViewSet
from apps.groups.models import GroupMembershipStatus

from .models import FileAccessPurpose, FileAccessTicket, FileResource
from .serializers import FileResourceSerializer
from .services import FileAccessService, accessible_files_for_user, user_can_access_file

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

    def perform_create(self, serializer):
        resource = serializer.save(uploaded_by=self.request.user)
        AuditLogService.log(
            actor=self.request.user,
            action=AuditAction.FILE_UPLOADED,
            target=resource,
            new_value={"visibility": resource.visibility, "sha256": resource.sha256},
            request=self.request,
        )


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
