from __future__ import annotations

import mimetypes
from pathlib import Path

from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework import filters, permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.views import APIView

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
        if not self.request.user.group_memberships.filter(
            group_id=group_id,
            status=GroupMembershipStatus.APPROVED,
            is_deleted=False,
        ).exists():
            raise PermissionDenied("You are not an approved member of this group.")
        return accessible_files_for_user(self.request.user).filter(group_id=group_id)


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
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    @extend_schema(auth=[], tags=["Protected Assets"], responses={200: OpenApiResponse(description="Inline protected file stream")})
    def get(self, request, token):
        with transaction.atomic():
            ticket = (
                FileAccessTicket.objects.select_for_update().select_related("file_resource", "user")
                .filter(token=token, is_deleted=False)
                .first()
            )
            if not ticket or not ticket.is_valid:
                raise Http404("The protected file link is invalid or expired.")
            resource = ticket.file_resource
            if not resource.file:
                raise Http404("File is unavailable.")
            ticket.use_count += 1
            ticket.save(update_fields=["use_count", "updated_at"])
        filename = Path(resource.file.name).name
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        response = FileResponse(resource.file.open("rb"), content_type=content_type)
        response["Content-Disposition"] = f'inline; filename="{filename}"'
        response["Cache-Control"] = "private, no-store, max-age=0"
        response["Pragma"] = "no-cache"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"
        return response
