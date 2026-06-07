from pathlib import Path

from django.http import FileResponse
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import filters
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.views import APIView

from apps.accounts.permissions import IsAdminOrITSupport
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.viewsets import StandardModelViewSet, StandardReadOnlyModelViewSet
from apps.groups.models import GroupMembershipStatus

from .models import FileResource
from .serializers import FileResourceSerializer
from .services import accessible_files_for_user, user_can_access_file


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
        if not self.request.user.group_memberships.filter(group_id=group_id, status=GroupMembershipStatus.APPROVED).exists():
            raise PermissionDenied("You are not an approved member of this group.")
        return accessible_files_for_user(self.request.user).filter(group_id=group_id)


class DashboardFileResourceViewSet(StandardModelViewSet):
    permission_classes = [IsAdminOrITSupport]
    serializer_class = FileResourceSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = FileResourceViewSet.filterset_fields
    search_fields = ["title", "description"]
    ordering_fields = ["created_at", "title", "file_size"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return FileResource.objects.filter(is_deleted=False).select_related(
            "uploaded_by", "university", "faculty", "major", "academic_year", "semester", "subject", "group"
        )

    def perform_create(self, serializer):
        file_resource = serializer.save(uploaded_by=self.request.user)
        AuditLogService.log(actor=self.request.user, action=AuditAction.FILE_UPLOADED, target=file_resource, request=self.request)

    def perform_update(self, serializer):
        file_resource = serializer.save()
        AuditLogService.log(actor=self.request.user, action=AuditAction.FILE_UPDATED, target=file_resource, request=self.request)

    def perform_destroy(self, instance):
        AuditLogService.log(actor=self.request.user, action=AuditAction.FILE_DELETED, target=instance, request=self.request)
        super().perform_destroy(instance)


class FileResourceProtectedView(APIView):
    serializer_class = FileResourceSerializer

    @extend_schema(tags=["Files"], responses={200: bytes})
    def get(self, request, pk: int):
        file_resource = FileResource.objects.filter(pk=pk, is_deleted=False).first()
        if file_resource is None:
            raise NotFound("File not found.")
        if not user_can_access_file(request.user, file_resource):
            raise PermissionDenied("You do not have access to this file.")
        if not file_resource.file:
            raise NotFound("File not found.")
        try:
            file_handle = file_resource.file.open("rb")
        except FileNotFoundError as exc:
            raise NotFound("File not found.") from exc

        AuditLogService.log(
            actor=request.user,
            action=AuditAction.FILE_ACCESSED,
            target=file_resource,
            new_value={"file_id": file_resource.id, "file_type": file_resource.file_type},
            request=request,
        )
        response = FileResponse(file_handle, as_attachment=False, filename=Path(file_resource.file.name).name)
        response["X-Content-Type-Options"] = "nosniff"
        return response
