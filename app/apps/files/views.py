from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import filters
from rest_framework.exceptions import PermissionDenied

from apps.accounts.permissions import IsAdminOrITSupport
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
        serializer.save(uploaded_by=self.request.user)
