from django.core.cache import cache
from django.db.models import Count, Q
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.views import APIView

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.accounts.permissions import CanAccessDashboard, Capability, PermissionService
from apps.common.responses import success_response
from apps.feedback.models import AppFeedback, FeedbackKind, FeedbackStatus
from apps.files.models import FileResource
from apps.groups.models import Group, GroupMembership, GroupMembershipStatus
from apps.printing.models import PrintOrder, PrintOrderStatus
from apps.support.models import SupportTicket, SupportTicketPriority, SupportTicketStatus
from apps.verification.models import VerificationRequest, VerificationStatus

STATS_CACHE_SECONDS = 30


class DashboardStatsSerializer(serializers.Serializer):
    users = serializers.DictField()
    printing = serializers.DictField()
    groups = serializers.DictField()
    files = serializers.DictField()
    support = serializers.DictField()
    feedback = serializers.DictField()


def _users() -> dict:
    counts = User.objects.filter(is_deleted=False).aggregate(
        total=Count("id"),
        students=Count("id", filter=Q(role=UserRole.STUDENT)),
        normal_users=Count("id", filter=Q(role=UserRole.NORMAL_USER)),
    )
    return {
        **counts,
        "verified_students": StudentProfile.objects.filter(
            is_deleted=False, verification_status=StudentVerificationStatus.APPROVED
        ).count(),
        "pending_verifications": VerificationRequest.objects.filter(
            is_deleted=False, status=VerificationStatus.PENDING
        ).count(),
    }


def _printing() -> dict:
    today = timezone.localdate()
    return PrintOrder.objects.filter(is_deleted=False).aggregate(
        total_orders=Count("id"),
        today_orders=Count("id", filter=Q(created_at__date=today)),
        pending_orders=Count("id", filter=Q(status__in=[PrintOrderStatus.SUBMITTED, PrintOrderStatus.UNDER_REVIEW])),
        ready_orders=Count("id", filter=Q(status=PrintOrderStatus.READY)),
        delivered_orders=Count("id", filter=Q(status=PrintOrderStatus.DELIVERED)),
    )


def _groups() -> dict:
    counts = Group.objects.filter(is_deleted=False).aggregate(
        total=Count("id"), active=Count("id", filter=Q(is_active=True))
    )
    return {
        **counts,
        "pending_join_requests": GroupMembership.objects.filter(
            is_deleted=False, status=GroupMembershipStatus.PENDING
        ).count(),
    }


def _files() -> dict:
    return FileResource.objects.filter(is_deleted=False).aggregate(
        total=Count("id"), active=Count("id", filter=Q(is_active=True))
    )


def _support() -> dict:
    return SupportTicket.objects.filter(is_deleted=False).aggregate(
        open_tickets=Count("id", filter=Q(status__in=[SupportTicketStatus.OPEN, SupportTicketStatus.IN_PROGRESS])),
        urgent_tickets=Count("id", filter=Q(priority=SupportTicketPriority.URGENT)),
    )


def _feedback() -> dict:
    closed = [FeedbackStatus.RESOLVED, FeedbackStatus.REJECTED, FeedbackStatus.DUPLICATE]
    return AppFeedback.objects.filter(is_deleted=False).aggregate(
        total=Count("id"),
        open_items=Count("id", filter=~Q(status__in=closed)),
        ratings=Count("id", filter=Q(kind=FeedbackKind.RATING)),
    )


# section -> (capabilities that unlock it, builder). A section the caller may not see is returned empty.
SECTIONS = {
    "users": ((Capability.USERS_MANAGE, Capability.VERIFICATION_REVIEW), _users),
    "printing": ((Capability.PRINTING_MANAGE,), _printing),
    "groups": ((Capability.GROUPS_MANAGE,), _groups),
    "files": ((Capability.FILES_MANAGE,), _files),
    "support": ((Capability.SUPPORT_MANAGE,), _support),
    "feedback": ((Capability.FEEDBACK_MANAGE,), _feedback),
}


def _section(name: str, builder) -> dict:
    key = f"dashboard:stats:{name}"
    value = cache.get(key)
    if value is None:
        value = builder()
        cache.set(key, value, STATS_CACHE_SECONDS)
    return value


class DashboardStatsView(APIView):
    permission_classes = [CanAccessDashboard]
    serializer_class = DashboardStatsSerializer

    @extend_schema(
        tags=["Dashboard"],
        responses={200: DashboardStatsSerializer},
        description=(
            "Aggregate counts, cached for 30 seconds and excluding soft-deleted rows. Each section is only "
            "populated when the caller holds the matching capability; otherwise it is an empty object."
        ),
    )
    def get(self, request):
        data = {
            name: _section(name, builder) if any(PermissionService.has(request.user, c) for c in caps) else {}
            for name, (caps, builder) in SECTIONS.items()
        }
        return success_response(data=data, request=request)
