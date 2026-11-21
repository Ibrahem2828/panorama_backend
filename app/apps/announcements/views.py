from django.http import Http404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiTypes, extend_schema
from rest_framework import filters
from rest_framework.views import APIView

from apps.accounts.permissions import CanManageAnnouncements, Capability, PermissionService
from apps.audit.mixins import AuditedModelWriteMixin
from apps.audit.models import AuditAction
from apps.common.image_delivery import image_etag, serve_stored_image
from apps.common.viewsets import StandardModelViewSet, StandardReadOnlyModelViewSet

from .models import Announcement
from .serializers import AnnouncementSerializer
from .services import announcements_for_user


class AnnouncementViewSet(StandardReadOnlyModelViewSet):
    serializer_class = AnnouncementSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = [
        "target_user_type",
        "target_university",
        "target_faculty",
        "target_major",
        "target_academic_year",
        "target_semester",
    ]
    search_fields = ["title", "description"]
    ordering_fields = ["created_at", "starts_at", "ends_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Announcement.objects.none()
        return announcements_for_user(self.request.user).select_related(
            "created_by",
            "target_university",
            "target_faculty",
            "target_major",
            "target_academic_year",
            "target_semester",
        )


class AnnouncementImageView(APIView):
    """The image of one announcement, behind authentication (``/media/`` is not published).

    Readers get it only when the announcement is in their own feed (``announcements_for_user``: active, in its date
    window, targeting their role and academic level); staff with ``announcements.manage`` see every non-deleted one."""

    @extend_schema(tags=["Announcements"], responses={(200, "image/*"): OpenApiTypes.BINARY, 304: None})
    def get(self, request, pk: int):
        if PermissionService.has(request.user, Capability.ANNOUNCEMENTS_MANAGE):
            rows = Announcement.objects.filter(is_deleted=False)
        else:
            rows = announcements_for_user(request.user)
        row = rows.filter(pk=pk).exclude(image="").first()
        if row is None or not row.image:
            raise Http404
        etag = image_etag(row.pk, row.image.name, row.updated_at.isoformat())
        return serve_stored_image(request, row.image, etag)


class DashboardAnnouncementViewSet(AuditedModelWriteMixin, StandardModelViewSet):
    permission_classes = [CanManageAnnouncements]
    audit_create_action = AuditAction.ANNOUNCEMENT_CREATED
    audit_update_action = AuditAction.ANNOUNCEMENT_UPDATED
    audit_delete_action = AuditAction.ANNOUNCEMENT_DELETED
    serializer_class = AnnouncementSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = AnnouncementViewSet.filterset_fields + ["is_active"]
    search_fields = ["title", "description"]
    ordering_fields = ["created_at", "starts_at", "ends_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return Announcement.objects.filter(is_deleted=False).select_related(
            "created_by",
            "target_university",
            "target_faculty",
            "target_major",
            "target_academic_year",
            "target_semester",
        )

    def audit_save_kwargs(self, *, creating: bool):
        return {"created_by": self.request.user} if creating else {}
