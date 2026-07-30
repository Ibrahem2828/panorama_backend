from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.views import APIView

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.accounts.permissions import CanAccessDashboard
from apps.common.responses import success_response
from apps.feedback.models import AppFeedback, FeedbackKind, FeedbackStatus
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
    feedback = serializers.DictField()


class DashboardStatsView(APIView):
    permission_classes = [CanAccessDashboard]
    serializer_class = DashboardStatsSerializer

    @extend_schema(tags=["Dashboard"], responses={200: DashboardStatsSerializer})
    def get(self, request):
        today = timezone.localdate()
        data = {
            "users": {
                "total": User.objects.count(),
                "students": User.objects.filter(role=UserRole.STUDENT).count(),
                "normal_users": User.objects.filter(role=UserRole.NORMAL_USER).count(),
                "verified_students": StudentProfile.objects.filter(verification_status=StudentVerificationStatus.APPROVED).count(),
                "pending_verifications": VerificationRequest.objects.filter(status=VerificationStatus.PENDING).count(),
            },
            "printing": {
                "total_orders": PrintOrder.objects.count(),
                "today_orders": PrintOrder.objects.filter(created_at__date=today).count(),
                "pending_orders": PrintOrder.objects.filter(status__in=[PrintOrderStatus.SUBMITTED, PrintOrderStatus.UNDER_REVIEW]).count(),
                "ready_orders": PrintOrder.objects.filter(status=PrintOrderStatus.READY).count(),
                "delivered_orders": PrintOrder.objects.filter(status=PrintOrderStatus.DELIVERED).count(),
            },
            "groups": {
                "total": Group.objects.count(),
                "active": Group.objects.filter(is_active=True).count(),
                "pending_join_requests": GroupMembership.objects.filter(status=GroupMembershipStatus.PENDING).count(),
            },
            "files": {
                "total": FileResource.objects.count(),
                "active": FileResource.objects.filter(is_active=True).count(),
            },
            "support": {
                "open_tickets": SupportTicket.objects.filter(status__in=[SupportTicketStatus.OPEN, SupportTicketStatus.IN_PROGRESS]).count(),
                "urgent_tickets": SupportTicket.objects.filter(priority=SupportTicketPriority.URGENT).count(),
            },
            "feedback": {
                "total": AppFeedback.objects.filter(is_deleted=False).count(),
                "open_items": AppFeedback.objects.filter(is_deleted=False).exclude(
                    status__in=[FeedbackStatus.RESOLVED, FeedbackStatus.REJECTED, FeedbackStatus.DUPLICATE]
                ).count(),
                "ratings": AppFeedback.objects.filter(kind=FeedbackKind.RATING, is_deleted=False).count(),
            },
        }
        return success_response(data=data, request=request)
