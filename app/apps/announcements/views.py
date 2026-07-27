from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters

from apps.accounts.permissions import CanManageAnnouncements
from apps.common.viewsets import StandardModelViewSet, StandardReadOnlyModelViewSet

from .models import Announcement
from .serializers import AnnouncementSerializer
from .services import announcements_for_user


class AnnouncementViewSet(StandardReadOnlyModelViewSet):
    serializer_class = AnnouncementSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["target_user_type", "target_university", "target_faculty", "target_major", "target_academic_year", "target_semester"]
    search_fields = ["title", "description"]
    ordering_fields = ["created_at", "starts_at", "ends_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Announcement.objects.none()
        return announcements_for_user(self.request.user).select_related(
            "created_by", "target_university", "target_faculty", "target_major", "target_academic_year", "target_semester"
        )


class DashboardAnnouncementViewSet(StandardModelViewSet):
    permission_classes = [CanManageAnnouncements]
    serializer_class = AnnouncementSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = AnnouncementViewSet.filterset_fields + ["is_active"]
    search_fields = ["title", "description"]
    ordering_fields = ["created_at", "starts_at", "ends_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return Announcement.objects.filter(is_deleted=False).select_related(
            "created_by", "target_university", "target_faculty", "target_major", "target_academic_year", "target_semester"
        )

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
