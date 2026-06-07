from django.utils import timezone
from django.db.models import Count, Q
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.views import APIView

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.accounts.permissions import IsAdminOrITSupport
from apps.common.responses import success_response
from apps.files.models import FileResource
from apps.groups.models import Group, GroupMembership, GroupMembershipStatus
from apps.printing.models import PrintOrder, PrintOrderStatus
from apps.support.models import SupportTicket, SupportTicketPriority, SupportTicketStatus
from apps.verification.models import VerificationRequest, VerificationStatus


class DashboardStatsSerializer(serializers.Serializer):
    users = serializers.DictField()
    printing = serializers.DictField()
    groups = serializers.DictField()
    files = serializers.DictField()
    support = serializers.DictField()


class DashboardStatsView(APIView):
    permission_classes = [IsAdminOrITSupport]
    serializer_class = DashboardStatsSerializer

    @extend_schema(tags=["Dashboard"], responses={200: DashboardStatsSerializer})
    def get(self, request):
        today = timezone.localdate()
        user_counts = User.objects.aggregate(
            total=Count("id"),
            students=Count("id", filter=Q(role=UserRole.STUDENT)),
            normal_users=Count("id", filter=Q(role=UserRole.NORMAL_USER)),
        )
        printing_counts = PrintOrder.objects.aggregate(
            total_orders=Count("id"),
            today_orders=Count("id", filter=Q(created_at__date=today)),
            pending_orders=Count("id", filter=Q(status__in=[PrintOrderStatus.SUBMITTED, PrintOrderStatus.UNDER_REVIEW])),
            ready_orders=Count("id", filter=Q(status=PrintOrderStatus.READY)),
            delivered_orders=Count("id", filter=Q(status=PrintOrderStatus.DELIVERED)),
        )
        group_counts = Group.objects.aggregate(
            total=Count("id"),
            active=Count("id", filter=Q(is_active=True)),
        )
        file_counts = FileResource.objects.aggregate(
            total=Count("id"),
            active=Count("id", filter=Q(is_active=True)),
        )
        support_counts = SupportTicket.objects.aggregate(
            open_tickets=Count("id", filter=Q(status__in=[SupportTicketStatus.OPEN, SupportTicketStatus.IN_PROGRESS])),
            urgent_tickets=Count("id", filter=Q(priority=SupportTicketPriority.URGENT)),
        )
        data = {
            "users": {
                "total": user_counts["total"],
                "students": user_counts["students"],
                "normal_users": user_counts["normal_users"],
                "verified_students": StudentProfile.objects.filter(verification_status=StudentVerificationStatus.APPROVED).count(),
                "pending_verifications": VerificationRequest.objects.filter(status=VerificationStatus.PENDING).count(),
            },
            "printing": {
                "total_orders": printing_counts["total_orders"],
                "today_orders": printing_counts["today_orders"],
                "pending_orders": printing_counts["pending_orders"],
                "ready_orders": printing_counts["ready_orders"],
                "delivered_orders": printing_counts["delivered_orders"],
            },
            "groups": {
                "total": group_counts["total"],
                "active": group_counts["active"],
                "pending_join_requests": GroupMembership.objects.filter(status=GroupMembershipStatus.PENDING).count(),
            },
            "files": {
                "total": file_counts["total"],
                "active": file_counts["active"],
            },
            "support": {
                "open_tickets": support_counts["open_tickets"],
                "urgent_tickets": support_counts["urgent_tickets"],
            },
        }
        return success_response(data=data)
