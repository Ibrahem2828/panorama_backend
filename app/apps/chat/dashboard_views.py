"""Staff moderation of reported chat messages (capability ``groups.manage``: the existing group-moderation role)."""

from __future__ import annotations

from django.db import transaction
from django.db.models import Count, Max, OuterRef, Q, Subquery
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import filters, serializers, status
from rest_framework.exceptions import APIException, NotFound
from rest_framework.views import APIView

from apps.accounts.permissions import CanManageGroups
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.idempotency import run_idempotent
from apps.common.responses import success_response
from apps.common.viewsets import StandardExplicitActionViewSet
from apps.groups.models import Group, GroupMembershipStatus

from .models import UNRESOLVED_REPORT_STATUSES, Message, MessageReport, MessageReportStatus
from .services import ChatMessageService

EXCERPT_LENGTH = 200


class DashboardMessageReportSerializer(serializers.ModelSerializer):
    reporter_id = serializers.IntegerField(source="reported_by_id", read_only=True)
    group_id = serializers.IntegerField(source="message.group_id", read_only=True)
    group_name = serializers.CharField(source="message.group.name", read_only=True)
    message_id = serializers.IntegerField(read_only=True)
    message_sender_id = serializers.IntegerField(source="message.sender_id", read_only=True)
    message_excerpt = serializers.SerializerMethodField()
    message_is_deleted = serializers.BooleanField(source="message.is_deleted", read_only=True)
    resolved_by_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = MessageReport
        fields = [
            "id",
            "status",
            "reason",
            "reporter_id",
            "group_id",
            "group_name",
            "message_id",
            "message_sender_id",
            "message_excerpt",
            "message_is_deleted",
            "resolved_by_id",
            "resolved_at",
            "resolution_note",
            "created_at",
        ]
        read_only_fields = fields

    def get_message_excerpt(self, report: MessageReport) -> str:
        content = report.message.content or ""
        return content if len(content) <= EXCERPT_LENGTH else content[: EXCERPT_LENGTH - 1] + "…"


class ResolveMessageReportSerializer(serializers.Serializer):
    ACTION_DISMISS = "dismiss"
    ACTION_DELETE_MESSAGE = "delete_message"

    action = serializers.ChoiceField(choices=[ACTION_DISMISS, ACTION_DELETE_MESSAGE])
    note = serializers.CharField(required=False, allow_blank=True, max_length=500)


class ReportAlreadyResolved(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "This report has already been resolved."
    default_code = "report_already_resolved"


class DashboardMessageReportViewSet(StandardExplicitActionViewSet):
    """``GET /dashboard/chat/reports/``: filter by ``status`` / ``group``; search the reason and message text."""

    permission_classes = [CanManageGroups]
    serializer_class = DashboardMessageReportSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["reason", "message__content"]
    ordering_fields = ["created_at", "status"]
    ordering = ["-created_at", "-id"]

    def get_queryset(self):
        queryset = MessageReport.objects.filter(is_deleted=False).select_related("message", "message__group")
        params = self.request.query_params
        status_filter = params.get("status")
        if status_filter:
            if status_filter == "open":  # "open" means everything still waiting for a decision
                queryset = queryset.filter(status__in=UNRESOLVED_REPORT_STATUSES)
            elif status_filter in MessageReportStatus.values:
                queryset = queryset.filter(status=status_filter)
            else:
                raise serializers.ValidationError({"status": "Unknown report status."})
        group = params.get("group")
        if group:
            try:
                queryset = queryset.filter(message__group_id=int(group))
            except ValueError as exc:
                raise serializers.ValidationError({"group": "A group id is required."}) from exc
        return queryset


class DashboardMessageReportResolveView(APIView):
    permission_classes = [CanManageGroups]
    serializer_class = ResolveMessageReportSerializer

    @extend_schema(
        tags=["Dashboard"],
        request=ResolveMessageReportSerializer,
        responses={200: DashboardMessageReportSerializer},
    )
    def post(self, request, pk: int):
        serializer = ResolveMessageReportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action = serializer.validated_data["action"]
        note = serializer.validated_data.get("note", "").strip()

        def write():
            with transaction.atomic():
                report = (
                    MessageReport.objects.select_for_update(of=("self",))
                    .filter(pk=pk, is_deleted=False)
                    .select_related("message")
                    .first()
                )
                if report is None:
                    raise NotFound("Report not found.")
                if report.status not in UNRESOLVED_REPORT_STATUSES:
                    raise ReportAlreadyResolved()
                message: Message = report.message
                now = timezone.now()
                if action == ResolveMessageReportSerializer.ACTION_DELETE_MESSAGE:
                    if not message.is_deleted:
                        ChatMessageService.delete_message(
                            message, actor=request.user, request=request, extra_audit={"report_id": report.pk}
                        )
                    # One decision closes every pending report about the same message.
                    MessageReport.objects.filter(
                        message=message, is_deleted=False, status__in=UNRESOLVED_REPORT_STATUSES
                    ).update(
                        status=MessageReportStatus.MESSAGE_DELETED,
                        resolved_by=request.user,
                        resolved_at=now,
                        resolution_note=note,
                        updated_at=now,
                    )
                else:
                    report.status = MessageReportStatus.DISMISSED
                    report.resolved_by = request.user
                    report.resolved_at = now
                    report.resolution_note = note
                    report.save(update_fields=["status", "resolved_by", "resolved_at", "resolution_note", "updated_at"])
                AuditLogService.log(
                    actor=request.user,
                    action=AuditAction.MESSAGE_REPORT_RESOLVED,
                    target=report,
                    new_value={
                        "action": action,
                        "message_id": message.pk,
                        "group_id": message.group_id,
                        "has_note": bool(note),
                    },
                    request=request,
                    critical=True,
                )
            report.refresh_from_db()
            return success_response(
                data=DashboardMessageReportSerializer(report).data,
                message="Report resolved.",
                request=request,
                code="CHAT_REPORT_RESOLVED",
            )

        return run_idempotent(request, f"dashboard-chat-report-resolve:{pk}", write)


class DashboardChatGroupSerializer(serializers.ModelSerializer):
    subject_name = serializers.CharField(source="subject.name", read_only=True, default=None)
    faculty_name = serializers.CharField(source="faculty.name", read_only=True, default=None)
    members = serializers.IntegerField(read_only=True)
    last_message_at = serializers.DateTimeField(read_only=True)
    last_message_excerpt = serializers.CharField(read_only=True, default="")
    open_reports = serializers.IntegerField(read_only=True)

    class Meta:
        model = Group
        fields = [
            "id",
            "name",
            "subject_name",
            "faculty_name",
            "is_active",
            "members",
            "last_message_at",
            "last_message_excerpt",
            "open_reports",
        ]
        read_only_fields = fields


class DashboardChatGroupViewSet(StandardExplicitActionViewSet):
    """The chat console's group list: ``GET /dashboard/chat/groups/`` (``search`` by group or subject, ``reported=1``).

    Opening a chat from here uses the ordinary message endpoints: staff with ``groups.manage`` may read and write in
    every group chat, and their messages carry their position title instead of a name.
    """

    permission_classes = [CanManageGroups]
    serializer_class = DashboardChatGroupSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ["name", "subject__name", "faculty__name"]

    def get_queryset(self):
        latest = Message.objects.filter(group=OuterRef("pk"), is_deleted=False).order_by("-created_at", "-id")
        queryset = (
            Group.objects.filter(is_deleted=False)
            .select_related("subject", "faculty")
            .annotate(
                members=Count(
                    "memberships", filter=Q(memberships__status=GroupMembershipStatus.APPROVED), distinct=True
                ),
                last_message_at=Max("messages__created_at", filter=Q(messages__is_deleted=False)),
                last_message_excerpt=Subquery(latest.values("content")[:1]),
                open_reports=Count(
                    "messages__reports",
                    filter=Q(
                        messages__reports__status__in=UNRESOLVED_REPORT_STATUSES, messages__reports__is_deleted=False
                    ),
                    distinct=True,
                ),
            )
            .order_by("-open_reports", "-last_message_at", "name")
        )
        if self.request.query_params.get("reported") in {"1", "true"}:
            queryset = queryset.filter(open_reports__gt=0)
        return queryset
