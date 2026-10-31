from __future__ import annotations

from datetime import datetime, time, timedelta

import django_filters
from django.db.models import Count, F, Max, Min, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import filters, mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.accounts.permissions import CanViewAudit
from apps.common.responses import success_response
from apps.common.viewsets import StandardReadOnlyModelViewSet

from .models import AuditLog, SystemActivity, SystemActivityKind
from .serializers import (
    AuditLogSerializer,
    SystemActivitySerializer,
    SystemActivitySummarySerializer,
    SystemActivityUserRollupSerializer,
)


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


def _day_start(value):
    return timezone.make_aware(datetime.combine(value, time.min))


class SystemActivityFilter(django_filters.FilterSet):
    user = django_filters.NumberFilter(field_name="user_id")
    role = django_filters.CharFilter(field_name="role")
    kind = django_filters.ChoiceFilter(choices=SystemActivityKind.choices)
    method = django_filters.CharFilter(method="filter_method")
    outcome = django_filters.ChoiceFilter(choices=[("success", "success"), ("error", "error")], method="filter_outcome")
    status_code = django_filters.NumberFilter(field_name="status_code")
    route = django_filters.CharFilter(field_name="route", lookup_expr="icontains")
    search = django_filters.CharFilter(method="filter_search")
    created_after = django_filters.DateFilter(method="filter_after", input_formats=["%Y-%m-%d"])
    created_before = django_filters.DateFilter(method="filter_before", input_formats=["%Y-%m-%d"])

    class Meta:
        model = SystemActivity
        fields: list[str] = []

    def filter_method(self, queryset, name, value):
        return queryset.filter(method=value.upper())

    def filter_outcome(self, queryset, name, value):
        return queryset.filter(status_code__gte=400) if value == "error" else queryset.filter(status_code__lt=400)

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(user__full_name__icontains=value)
            | Q(user__email__icontains=value)
            | Q(user_label__icontains=value)
            | Q(route__icontains=value)
            | Q(action_label__icontains=value)
        )

    def filter_after(self, queryset, name, value):
        return queryset.filter(created_at__gte=_day_start(value))

    def filter_before(self, queryset, name, value):  # inclusive: the whole of that day
        return queryset.filter(created_at__lt=_day_start(value + timedelta(days=1)))


_USER_ORDERING = {
    "last_activity_at": "last_activity_at",
    "requests_24h": "requests_24h",
    "requests_7d": "requests_7d",
    "failed_logins_24h": "failed_logins_24h",
    "user": "full_name",
}


class DashboardSystemActivityViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Who asked the system for what: authenticated writes, logins, logouts and denied calls (read-only)."""

    permission_classes = [CanViewAudit]
    serializer_class = SystemActivitySerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_class = SystemActivityFilter
    ordering_fields = ["created_at", "user"]
    ordering = ["-created_at", "-id"]

    def get_queryset(self):
        return SystemActivity.objects.select_related("user")

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @extend_schema(tags=["Dashboard"], responses={200: SystemActivitySummarySerializer})
    @action(detail=False, methods=["get"], url_path="summary", pagination_class=None)
    def summary(self, request):
        """Counters for the same filters as the list (ordering and pagination ignored)."""

        queryset = self.filter_queryset(self.get_queryset()).order_by()
        agg = queryset.aggregate(
            total=Count("pk"),
            errors=Count("pk", filter=Q(status_code__gte=400)),
            active_users=Count("user_id", distinct=True),
            first=Min("created_at"),
            last=Max("created_at"),
            **{f"kind_{kind}": Count("pk", filter=Q(kind=kind)) for kind in SystemActivityKind.values},
        )
        params = request.query_params
        data = {
            "total": agg["total"],
            "errors": agg["errors"],
            "failed_logins": agg[f"kind_{SystemActivityKind.LOGIN_FAILED}"],
            "active_users": agg["active_users"],
            "by_kind": {kind: agg[f"kind_{kind}"] for kind in SystemActivityKind.values},
            "window": {
                "from": params.get("created_after") or (agg["first"].isoformat() if agg["first"] else None),
                "to": params.get("created_before") or (agg["last"].isoformat() if agg["last"] else None),
            },
        }
        return success_response(data=data, request=request)

    @extend_schema(tags=["Dashboard"], responses={200: SystemActivityUserRollupSerializer(many=True)})
    @action(detail=False, methods=["get"], url_path="users", filter_backends=[])
    def users(self, request):
        """One row per user with any recorded activity (staff and students), most recently active first."""

        now = timezone.now()
        since_24h, since_7d = now - timedelta(hours=24), now - timedelta(days=7)
        latest = SystemActivity.objects.filter(user=OuterRef("pk")).order_by("-created_at", "-id")
        rel = "system_activities__"
        queryset = User.objects.annotate(
            last_activity_at=Max(f"{rel}created_at"),
            requests_24h=Count(f"{rel}pk", filter=Q(**{f"{rel}kind": "request", f"{rel}created_at__gte": since_24h})),
            requests_7d=Count(f"{rel}pk", filter=Q(**{f"{rel}kind": "request", f"{rel}created_at__gte": since_7d})),
            failed_logins_24h=Count(
                f"{rel}pk", filter=Q(**{f"{rel}kind": "login_failed", f"{rel}created_at__gte": since_24h})
            ),
            last_action_label=Coalesce(Subquery(latest.values("action_label")[:1]), Value("")),
            last_ip=Coalesce(Subquery(latest.values("ip")[:1]), Value("")),
        ).filter(last_activity_at__isnull=False)
        role = request.query_params.get("role")
        if role:
            if role not in UserRole.values:
                raise ValidationError({"role": "Unknown role."})
            queryset = queryset.filter(role=role)
        search = (request.query_params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(Q(full_name__icontains=search) | Q(email__icontains=search))
        ordering = request.query_params.get("ordering") or "-last_activity_at"
        key = ordering.lstrip("-")
        if key not in _USER_ORDERING:
            raise ValidationError({"ordering": f"Use one of: {', '.join(sorted(_USER_ORDERING))}."})
        order_field = F(_USER_ORDERING[key])
        order = order_field.desc(nulls_last=True) if ordering.startswith("-") else order_field.asc(nulls_last=True)
        page = self.paginate_queryset(queryset.order_by(order, "pk"))
        rows = [
            {
                "user": {
                    "id": u.pk,
                    "full_name": u.full_name,
                    "email": u.email,
                    "role": u.role,
                    "job_title": u.job_title,
                },
                "last_activity_at": u.last_activity_at,
                "requests_24h": u.requests_24h,
                "requests_7d": u.requests_7d,
                "failed_logins_24h": u.failed_logins_24h,
                "last_action_label": u.last_action_label,
                "last_ip": u.last_ip,
            }
            for u in page or []
        ]
        return self.get_paginated_response(rows)


__all__ = ["DashboardAuditLogViewSet", "DashboardSystemActivityViewSet"]
