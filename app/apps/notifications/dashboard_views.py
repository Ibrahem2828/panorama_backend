"""Dashboard side of the notification platform: the quick-access counters and the delivery health page."""

from __future__ import annotations

from datetime import timedelta

from django.db import models
from django.db.models import Count
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.views import APIView

from apps.accounts.permissions import CanManageSystem, Capability, CapabilityPermission, PermissionService
from apps.common.responses import success_response

from .models import DeviceToken, Notification, PushDelivery, PushDeliveryStatus


class CanAccessDashboard(CapabilityPermission):
    capability = Capability.DASHBOARD_ACCESS


RANGES = {"24h": timedelta(hours=24), "7d": timedelta(days=7)}


def _counters(user) -> dict[str, int]:
    """Numbers staff want at a glance, only for the areas the caller may act on (a missing key means "not yours")."""

    has = lambda capability: PermissionService.has(user, capability)  # noqa: E731
    counts: dict[str, int] = {}
    if has("enrollment.review"):
        from apps.enrollment.models import EnrollmentRequest, EnrollmentStatus

        counts["enrollment_pending"] = EnrollmentRequest.objects.filter(
            status=EnrollmentStatus.PENDING, is_deleted=False
        ).count()
    if has("verification.review"):
        from apps.verification.models import VerificationRequest, VerificationStatus

        counts["verification_pending"] = VerificationRequest.objects.filter(
            status=VerificationStatus.PENDING, is_deleted=False
        ).count()
    if has("groups.manage"):
        from apps.chat.models import UNRESOLVED_REPORT_STATUSES, MessageReport

        counts["chat_reports_open"] = MessageReport.objects.filter(
            status__in=UNRESOLVED_REPORT_STATUSES, is_deleted=False
        ).count()
    if has("printing.manage"):
        from apps.finance.models import InventoryItem
        from apps.printing.models import PrintOrder, PrintOrderStatus

        counts["print_orders_new"] = PrintOrder.objects.filter(
            status=PrintOrderStatus.SUBMITTED, is_deleted=False
        ).count()
        counts["low_stock"] = InventoryItem.objects.filter(
            is_active=True,
            is_deleted=False,
            reorder_level__gt=0,
            quantity__lte=models.F("reorder_level"),
        ).count()
    if has("support.manage"):
        from apps.support.models import SupportTicket, SupportTicketStatus

        counts["support_open"] = SupportTicket.objects.filter(
            status__in=(SupportTicketStatus.OPEN, SupportTicketStatus.WAITING_USER), is_deleted=False
        ).count()
    if has("finance.manage"):
        from apps.finance.models import PurchaseReminder
        from apps.finance.services import PurchaseReminderService

        counts["purchase_reminders_due"] = sum(
            1
            for reminder in PurchaseReminder.objects.filter(is_deleted=False, is_active=True)
            if PurchaseReminderService.status(reminder)["is_due"]
        )
    return counts


class QuickAccessView(APIView):
    """``GET /dashboard/quick-access/``: live counters behind the dashboard's quick-actions panel and command palette."""

    permission_classes = [CanAccessDashboard]

    @extend_schema(tags=["Dashboard notifications"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        unread = Notification.objects.filter(user=request.user, is_deleted=False, is_read=False).filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=timezone.now())
        )
        counts = _counters(request.user)
        counts["notifications_unread"] = unread.count()
        return success_response(data={"counts": counts, "generated_at": timezone.now()}, request=request)


class NotificationHealthView(APIView):
    """``GET /dashboard/notifications/health/?range=24h|7d`` (system administrators): what was sent, delivered and lost."""

    permission_classes = [CanManageSystem]

    @extend_schema(
        tags=["Dashboard notifications"],
        parameters=[OpenApiParameter("range", str, enum=["24h", "7d"], required=False)],
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request):
        window = request.query_params.get("range", "24h")
        window = window if window in RANGES else "24h"
        since = timezone.now() - RANGES[window]
        rows = PushDelivery.objects.filter(created_at__gte=since)
        by_status = {item["status"]: item["n"] for item in rows.values("status").annotate(n=Count("id"))}
        for status in PushDeliveryStatus.values:
            by_status.setdefault(status, 0)
        settled = by_status["delivered"] + by_status["failed"] + by_status["dead"]
        by_category: dict[str, dict[str, int]] = {}
        for item in rows.values("category", "status").annotate(n=Count("id")):
            by_category.setdefault(item["category"] or "general", {})[item["status"]] = item["n"]
        errors = rows.exclude(error_code="").values("error_code").annotate(n=Count("id")).order_by("-n")[:8]
        recent_failures = list(
            rows.filter(status__in=(PushDeliveryStatus.FAILED, PushDeliveryStatus.DEAD))
            .order_by("-created_at")
            .values("id", "category", "status", "error_code", "created_at")[:10]
        )
        data = {
            "range": window,
            "since": since,
            "pushes": {
                "total": rows.count(),
                **by_status,
                # Delivery rate is measured on pushes whose receipt is known; pending ones are not counted against it.
                "delivery_rate": round(by_status["delivered"] / settled, 4) if settled else None,
                "awaiting_receipt": by_status["sent"],
            },
            "by_category": by_category,
            "top_errors": list(errors),
            "recent_failures": recent_failures,
            "devices": {
                "active": DeviceToken.objects.filter(is_active=True, is_deleted=False).count(),
                "deactivated": DeviceToken.objects.filter(is_active=False, is_deleted=False).count(),
                "dead_in_range": by_status["dead"],
            },
            "in_app": {
                "created": Notification.objects.filter(created_at__gte=since).count(),
                "unread": Notification.objects.filter(is_read=False, is_deleted=False).count(),
            },
        }
        return success_response(data=data, request=request)
