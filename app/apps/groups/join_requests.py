"""Dashboard screen for group join requests (``groups.manage``): one paginated list over every group, a summary and a
bulk decision. Decisions go through ``GroupMembershipService.review``, the same path as the per-membership actions.

Joining by request no longer exists for students (see ``GroupJoinEnrollmentOnly``); these rows are the legacy pending
requests plus every later decision, kept so staff can still review them.
"""

from __future__ import annotations

import logging
from typing import Any

import django_filters
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import generics, serializers
from rest_framework.exceptions import APIException
from rest_framework.views import APIView

from apps.accounts.permissions import CanManageGroups
from apps.common.filters import DateBoundFilter, MappedOrderingFilter, StrictIntegerFilter
from apps.common.responses import success_response
from apps.common.viewsets import StandardReadOnlyModelViewSet

from .models import GroupMembership, GroupMembershipStatus
from .services import GroupMembershipService
from .views import GROUP_KINDS, group_kind_q

logger = logging.getLogger(__name__)

JOIN_REQUEST_STATUSES = (
    GroupMembershipStatus.PENDING,
    GroupMembershipStatus.APPROVED,
    GroupMembershipStatus.REJECTED,
    GroupMembershipStatus.BLOCKED,
    GroupMembershipStatus.LEFT,
)
BULK_MAX = 100


def _base_queryset():
    return GroupMembership.objects.filter(is_deleted=False).select_related(
        "user",
        "user__student_profile",
        "group",
        "group__faculty",
        "group__academic_year",
        "group__semester",
        "group__subject",
        "group__subject__faculty",
        "group__subject__academic_year",
        "group__subject__semester",
        "reviewed_by",
    )


class JoinRequestBaseFilter(django_filters.FilterSet):
    group = StrictIntegerFilter(field_name="group_id")
    subject = StrictIntegerFilter(field_name="group__subject_id")
    faculty = StrictIntegerFilter(method="filter_faculty")
    academic_year = StrictIntegerFilter(method="filter_academic_year")
    semester = StrictIntegerFilter(method="filter_semester")
    kind = django_filters.ChoiceFilter(choices=[(k, k) for k in GROUP_KINDS], method="filter_kind")
    search = django_filters.CharFilter(method="filter_search")
    created_after = DateBoundFilter(field_name="created_at", bound="after")
    created_before = DateBoundFilter(field_name="created_at", bound="before")

    class Meta:
        model = GroupMembership
        fields: list[str] = []

    # a subject group may leave the level fields empty, so fall back to the subject's own faculty/year/semester
    def filter_faculty(self, queryset, name, value):
        return queryset.filter(
            Q(group__faculty_id=value) | Q(group__faculty__isnull=True, group__subject__faculty_id=value)
        )

    def filter_academic_year(self, queryset, name, value):
        return queryset.filter(
            Q(group__academic_year_id=value)
            | Q(group__academic_year__isnull=True, group__subject__academic_year_id=value)
        )

    def filter_semester(self, queryset, name, value):
        return queryset.filter(
            Q(group__semester_id=value) | Q(group__semester__isnull=True, group__subject__semester_id=value)
        )

    def filter_kind(self, queryset, name, value):
        return queryset.filter(group_kind_q(value, prefix="group__")) if value else queryset

    def filter_search(self, queryset, name, value):
        term = " ".join((value or "").split())
        if not term:
            return queryset
        return queryset.filter(
            Q(user__full_name__icontains=term)
            | Q(user__email__icontains=term)
            | Q(user__student_profile__student_number__icontains=term)
            | Q(group__name__icontains=term)
        )


class JoinRequestFilter(JoinRequestBaseFilter):
    status = django_filters.ChoiceFilter(choices=[(s.value, s.label) for s in JOIN_REQUEST_STATUSES])
    ordering = MappedOrderingFilter(mapping={"created_at": "created_at"}, default="-created_at")

    def __init__(self, data=None, *args, **kwargs):
        if data is not None and not data.get("status"):
            data = data.copy()
            data["status"] = GroupMembershipStatus.PENDING
        super().__init__(data, *args, **kwargs)


def _brief(obj) -> dict | None:
    return {"id": obj.pk, "name": obj.name} if obj is not None else None


class JoinRequestSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()
    group = serializers.SerializerMethodField()
    reviewed_by = serializers.SerializerMethodField()
    note = serializers.SerializerMethodField()
    status = serializers.ChoiceField(choices=[(s.value, s.label) for s in JOIN_REQUEST_STATUSES], read_only=True)

    class Meta:
        model = GroupMembership
        fields = ["id", "created_at", "status", "user", "group", "reviewed_by", "reviewed_at", "note"]
        read_only_fields = fields

    def get_user(self, obj) -> dict[str, Any]:
        profile = getattr(obj.user, "student_profile", None)
        return {
            "id": obj.user_id,
            "full_name": obj.user.full_name,
            "email": obj.user.email,
            "student_number": profile.student_number if profile else "",
            "verification_status": profile.verification_status if profile else None,
        }

    def get_group(self, obj) -> dict[str, Any]:
        group = obj.group
        subject = group.subject
        faculty = group.faculty or (subject.faculty if subject else None)
        year = group.academic_year or (subject.academic_year if subject else None)
        semester = group.semester or (subject.semester if subject else None)
        return {
            "id": group.pk,
            "name": group.name,
            "kind": "subject" if subject else ("faculty" if group.faculty_id else "general"),
            "subject": _brief(subject),
            "faculty": _brief(faculty),
            "academic_year": _brief(year),
            "semester": _brief(semester),
        }

    def get_reviewed_by(self, obj) -> dict[str, Any] | None:
        reviewer = obj.reviewed_by
        return {"id": reviewer.pk, "full_name": reviewer.full_name} if reviewer else None

    def get_note(self, obj) -> str | None:
        # The decision note lives in the audit log and the student's notification; the membership row has no column.
        return None


class JoinRequestSummarySerializer(serializers.Serializer):
    pending = serializers.IntegerField()
    approved_today = serializers.IntegerField()
    rejected_today = serializers.IntegerField()


class JoinRequestViewSet(StandardReadOnlyModelViewSet):
    """``GET dashboard/group-join-requests/``.

    Filters: ``status`` (pending|approved|rejected|blocked|left, default pending), ``group``, ``faculty``,
    ``academic_year``, ``semester``, ``subject``, ``kind`` (subject|faculty|general), ``search`` (student name, e-mail,
    student number, group name), ``created_after``/``created_before`` (inclusive YYYY-MM-DD), ``ordering``
    (``created_at`` or ``-created_at``, default ``-created_at``).
    """

    permission_classes = [CanManageGroups]
    serializer_class = JoinRequestSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = JoinRequestFilter

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return GroupMembership.objects.none()
        return _base_queryset()


class JoinRequestSummaryView(generics.GenericAPIView):
    """``GET dashboard/group-join-requests/summary/``: counts under the same filters as the list except ``status``."""

    permission_classes = [CanManageGroups]
    serializer_class = JoinRequestSummarySerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = JoinRequestBaseFilter
    pagination_class = None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return GroupMembership.objects.none()
        return _base_queryset().order_by()

    @extend_schema(tags=["Dashboard"], responses={200: JoinRequestSummarySerializer})
    def get(self, request):
        start_of_day = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
        counts = self.filter_queryset(self.get_queryset()).aggregate(
            pending=Count("pk", filter=Q(status=GroupMembershipStatus.PENDING)),
            approved_today=Count("pk", filter=Q(status=GroupMembershipStatus.APPROVED, reviewed_at__gte=start_of_day)),
            rejected_today=Count("pk", filter=Q(status=GroupMembershipStatus.REJECTED, reviewed_at__gte=start_of_day)),
        )
        return success_response(data=counts, message="Join request summary", request=request)


class JoinRequestBulkDecisionSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=False, max_length=BULK_MAX)
    decision = serializers.ChoiceField(choices=["approve", "reject"])
    note = serializers.CharField(required=False, allow_blank=True, max_length=500)


class BulkItemResultSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    ok = serializers.BooleanField()
    error = serializers.CharField(required=False)


class BulkDecisionResponseSerializer(serializers.Serializer):
    results = BulkItemResultSerializer(many=True)
    succeeded = serializers.IntegerField()
    failed = serializers.IntegerField()


def _error_text(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        code = detail.get("code")
        detail = code if code else next(iter(detail.values()), None)
    if isinstance(detail, list) and detail:
        detail = detail[0]
    return str(detail) if detail else exc.__class__.__name__


class JoinRequestBulkDecisionView(APIView):
    """Approve or reject many pending requests. Every item is its own transaction through the same service as the
    single approve/reject actions, so one failing item never rolls back the others."""

    permission_classes = [CanManageGroups]
    serializer_class = JoinRequestBulkDecisionSerializer

    @extend_schema(
        tags=["Dashboard"], request=JoinRequestBulkDecisionSerializer, responses={200: BulkDecisionResponseSerializer}
    )
    def post(self, request):
        serializer = JoinRequestBulkDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        ids = list(dict.fromkeys(data["ids"]))
        target = GroupMembershipStatus.APPROVED if data["decision"] == "approve" else GroupMembershipStatus.REJECTED
        memberships = {m.pk: m for m in GroupMembership.objects.filter(pk__in=ids, is_deleted=False)}
        results: list[dict[str, Any]] = []
        for pk in ids:
            membership = memberships.get(pk)
            if membership is None:
                results.append({"id": pk, "ok": False, "error": "NOT_FOUND"})
                continue
            try:
                with transaction.atomic():
                    GroupMembershipService.review(
                        membership,
                        request.user,
                        target,
                        request=request,
                        note=data.get("note", ""),
                        only_if_pending=True,
                    )
                results.append({"id": pk, "ok": True})
            except APIException as exc:
                results.append({"id": pk, "ok": False, "error": _error_text(exc)})
            except Exception:  # noqa: BLE001 - one broken item must not stop the batch; details go to the log
                logger.exception("Bulk join-request decision failed for membership %s", pk)
                results.append({"id": pk, "ok": False, "error": "UNEXPECTED_ERROR"})
        succeeded = sum(1 for item in results if item["ok"])
        return success_response(
            data={"results": results, "succeeded": succeeded, "failed": len(results) - succeeded},
            message="Bulk decision processed",
            request=request,
        )
