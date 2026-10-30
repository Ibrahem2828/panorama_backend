"""Staff moderation of reported chat messages (capability ``groups.manage``: the existing group-moderation role)."""

from __future__ import annotations

import django_filters
from django.db import transaction
from django.db.models import OuterRef, Subquery
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_field
from rest_framework import filters, serializers, status
from rest_framework.exceptions import APIException, NotFound
from rest_framework.views import APIView

from apps.accounts.permissions import CanManageGroups
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.filters import (
    DateBoundFilter,
    MappedOrderingFilter,
    StrictBooleanFilter,
    StrictIntegerFilter,
    count_subquery,
)
from apps.common.idempotency import run_idempotent
from apps.common.responses import success_response
from apps.common.viewsets import StandardExplicitActionViewSet
from apps.groups.models import Group, GroupMembership, GroupMembershipStatus

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
    kind = serializers.SerializerMethodField()
    members = serializers.IntegerField(read_only=True)
    last_message_at = serializers.DateTimeField(read_only=True)
    last_message_excerpt = serializers.CharField(read_only=True, default="")
    open_reports = serializers.IntegerField(read_only=True)

    class Meta:
        model = Group
        fields = [
            "id",
            "name",
            "kind",
            "subject_name",
            "faculty_name",
            "is_active",
            "members",
            "last_message_at",
            "last_message_excerpt",
            "open_reports",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.ChoiceField(choices=["subject", "faculty", "general"]))
    def get_kind(self, obj) -> str:
        if obj.subject_id:
            return "subject"
        return "faculty" if obj.faculty_id else "general"


class DashboardChatGroupFilter(django_filters.FilterSet):
    faculty = StrictIntegerFilter(field_name="faculty_id")
    major = StrictIntegerFilter(field_name="major_id")
    academic_year = StrictIntegerFilter(field_name="academic_year_id")
    semester = StrictIntegerFilter(field_name="semester_id")
    subject = StrictIntegerFilter(field_name="subject_id")
    kind = django_filters.ChoiceFilter(
        choices=[(k, k) for k in ("subject", "faculty", "general")], method="filter_kind"
    )
    has_messages = StrictBooleanFilter(method="filter_has_messages")
    active_after = DateBoundFilter(field_name="last_message_at", bound="after")
    has_open_reports = StrictBooleanFilter(method="filter_has_open_reports")
    #: Aliases of ``has_open_reports=true`` (``reported`` is what the first dashboard build sent); false = unfiltered.
    reported = StrictBooleanFilter(method="filter_reported_only")
    reported_only = StrictBooleanFilter(method="filter_reported_only")
    ordering = MappedOrderingFilter(
        mapping={
            "last_message_at": "last_message_at",
            "open_reports": "open_reports",
            "members": "members",
            "name": "name",
        },
        default="-open_reports,-last_message_at,name",
    )

    class Meta:
        model = Group
        fields: list[str] = []

    def filter_kind(self, queryset, name, value):
        from apps.groups.views import group_kind_q

        return queryset.filter(group_kind_q(value)) if value else queryset

    def filter_has_messages(self, queryset, name, value):
        if value is None:
            return queryset
        return queryset.filter(last_message_at__isnull=not value)

    def filter_has_open_reports(self, queryset, name, value):
        if value is None:
            return queryset
        return queryset.filter(open_reports__gt=0) if value else queryset.filter(open_reports=0)

    def filter_reported_only(self, queryset, name, value):
        return queryset.filter(open_reports__gt=0) if value else queryset


class DashboardChatGroupViewSet(StandardExplicitActionViewSet):
    """The chat console's group list: ``GET /dashboard/chat/groups/``.

    Filters: ``search`` (group, subject or faculty name), ``faculty``, ``major``, ``academic_year``, ``semester``,
    ``subject``, ``kind`` (subject|faculty|general), ``has_messages``, ``active_after`` (last message on/after
    YYYY-MM-DD), ``has_open_reports`` (``reported``/``reported_only`` = true are aliases).
    ``ordering``: ``last_message_at``, ``open_reports``, ``members``, ``name`` (``-`` = descending; nulls last);
    default: most open reports, then latest activity.

    Opening a chat from here uses the ordinary message endpoints: staff with ``groups.manage`` may read and write in
    every group chat, and their messages carry their position title instead of a name.
    """

    permission_classes = [CanManageGroups]
    serializer_class = DashboardChatGroupSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_class = DashboardChatGroupFilter
    search_fields = ["name", "subject__name", "faculty__name"]

    def get_queryset(self):
        live_messages = Message.objects.filter(is_deleted=False)
        latest = live_messages.filter(group=OuterRef("pk")).order_by("-created_at", "-id")
        return (
            Group.objects.filter(is_deleted=False)
            .select_related("subject", "faculty")
            .annotate(
                members=count_subquery(
                    GroupMembership.objects.filter(status=GroupMembershipStatus.APPROVED, is_deleted=False),
                    "pk",
                    group_field="group_id",
                ),
                last_message_at=Subquery(latest.values("created_at")[:1]),
                last_message_excerpt=Subquery(latest.values("content")[:1]),
                open_reports=count_subquery(
                    MessageReport.objects.filter(status__in=UNRESOLVED_REPORT_STATUSES, is_deleted=False),
                    "pk",
                    group_field="message__group_id",
                ),
            )
        )
