from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters

from apps.accounts.permissions import CanViewAudit
from apps.common.viewsets import StandardReadOnlyModelViewSet

from .models import AuditLog
from .serializers import AuditLogSerializer


class DashboardAuditLogViewSet(StandardReadOnlyModelViewSet):
    permission_classes = [CanViewAudit]
    serializer_class = AuditLogSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["action", "actor", "target_type", "target_id"]
    search_fields = ["action", "target_type", "target_id", "actor__email", "actor__full_name"]
    ordering_fields = ["created_at", "action"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return AuditLog.objects.filter(is_deleted=False).select_related("actor")
