"""``GET dashboard/students/`` (+ ``summary/``): the students list with every slicing filter the dashboard needs.

A "student" is an account with role ``student``/``normal_user`` that owns a (live) ``StudentProfile``; staff accounts
never show up here. All counts are correlated subqueries, so the page is a fixed number of queries whatever its size.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import django_filters
from django.db.models import Count, Exists, OuterRef, Q, QuerySet
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiTypes, extend_schema, extend_schema_field
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError

from apps.common.filters import (
    CsvChoiceFilter,
    DateBoundFilter,
    MappedOrderingFilter,
    PositiveIntegerFilter,
    StrictBooleanFilter,
    StrictIntegerFilter,
    count_subquery,
)
from apps.common.responses import success_response

from .choices import StudentVerificationStatus, UserRole
from .dashboard_views import CanViewStudentDetail
from .models import User

STUDENT_ROLES = (UserRole.STUDENT, UserRole.NORMAL_USER)


def students_queryset() -> QuerySet[User]:
    """Live student accounts (no staff, no soft-deleted users or profiles)."""

    return User.objects.filter(
        is_deleted=False,
        role__in=STUDENT_ROLES,
        student_profile__isnull=False,
        student_profile__is_deleted=False,
    )


def _approved_memberships():
    from apps.groups.models import GroupMembership, GroupMembershipStatus

    return GroupMembership.objects.filter(
        status=GroupMembershipStatus.APPROVED, is_deleted=False, group__is_deleted=False
    )


def _pending_enrollments():
    from apps.enrollment.models import EnrollmentRequest, EnrollmentStatus

    return EnrollmentRequest.objects.filter(status=EnrollmentStatus.PENDING, is_deleted=False)


class StudentFilter(django_filters.FilterSet):
    search = django_filters.CharFilter(method="filter_search", label="Name, e-mail, phone or student number")
    verification_status = CsvChoiceFilter(
        field_name="student_profile__verification_status", choices=StudentVerificationStatus.values
    )
    is_verified = StrictBooleanFilter(method="filter_is_verified")
    university = StrictIntegerFilter(field_name="student_profile__university_id")
    faculty = StrictIntegerFilter(field_name="student_profile__faculty_id")
    major = StrictIntegerFilter(field_name="student_profile__major_id")
    academic_year = StrictIntegerFilter(field_name="student_profile__academic_year_id")
    semester = StrictIntegerFilter(field_name="student_profile__semester_id")
    is_active = StrictBooleanFilter(field_name="is_active")
    needs_term_confirmation = StrictBooleanFilter(field_name="student_profile__needs_term_confirmation")
    has_pending_enrollment = StrictBooleanFilter(method="filter_has_pending_enrollment")
    group = StrictIntegerFilter(method="filter_group")
    no_groups = StrictBooleanFilter(method="filter_no_groups")
    registered_after = DateBoundFilter(field_name="created_at", bound="after")
    registered_before = DateBoundFilter(field_name="created_at", bound="before")
    inactive_days = PositiveIntegerFilter(method="filter_inactive_days", min_value=0)
    ordering = MappedOrderingFilter(
        mapping={
            "full_name": "full_name",
            "created_at": "created_at",
            "last_login": "last_login",
            "student_number": "student_profile__student_number",
        },
        default="-created_at",
    )

    class Meta:
        model = User
        fields: list[str] = []

    def filter_search(self, queryset, name, value):
        term = " ".join((value or "").split())
        if not term:
            return queryset
        return queryset.filter(
            Q(full_name__icontains=term)
            | Q(email__icontains=term)
            | Q(phone_number__icontains=term)
            | Q(student_profile__student_number__icontains=term)
        )

    def filter_is_verified(self, queryset, name, value):
        approved = Q(student_profile__verification_status=StudentVerificationStatus.APPROVED)
        if value is None:
            return queryset
        return queryset.filter(approved) if value else queryset.exclude(approved)

    def filter_has_pending_enrollment(self, queryset, name, value):
        if value is None:
            return queryset
        pending = Exists(_pending_enrollments().filter(user=OuterRef("pk")))
        return queryset.filter(pending) if value else queryset.filter(~pending)

    def filter_group(self, queryset, name, value):
        if value is None:
            return queryset
        return queryset.filter(Exists(_approved_memberships().filter(user=OuterRef("pk"), group_id=value)))

    def filter_no_groups(self, queryset, name, value):
        if value is None:
            return queryset
        member = Exists(_approved_memberships().filter(user=OuterRef("pk")))
        return queryset.filter(~member) if value else queryset.filter(member)

    def filter_inactive_days(self, queryset, name, value):
        if value is None:
            return queryset
        cutoff = timezone.now() - timedelta(days=value)
        return queryset.filter(Q(last_login__isnull=True) | Q(last_login__lte=cutoff))


class NamedRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class DashboardStudentSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    full_name = serializers.CharField(read_only=True)
    email = serializers.EmailField(read_only=True)
    phone_number = serializers.CharField(read_only=True)
    student_number = serializers.CharField(source="student_profile.student_number", read_only=True)
    verification_status = serializers.CharField(source="student_profile.verification_status", read_only=True)
    is_verified = serializers.SerializerMethodField()
    university = serializers.SerializerMethodField()
    faculty = serializers.SerializerMethodField()
    major = serializers.SerializerMethodField()
    academic_year = serializers.SerializerMethodField()
    semester = serializers.SerializerMethodField()
    is_active = serializers.BooleanField(read_only=True)
    needs_term_confirmation = serializers.BooleanField(source="student_profile.needs_term_confirmation", read_only=True)
    last_login = serializers.DateTimeField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    groups_count = serializers.IntegerField(read_only=True)
    pending_enrollments = serializers.IntegerField(read_only=True)

    @staticmethod
    def _named(obj) -> dict[str, Any] | None:
        return {"id": obj.pk, "name": obj.name} if obj is not None else None

    def get_is_verified(self, obj) -> bool:
        return obj.student_profile.verification_status == StudentVerificationStatus.APPROVED

    @extend_schema_field(NamedRefSerializer(allow_null=True))
    def get_university(self, obj):
        return self._named(obj.student_profile.university)

    @extend_schema_field(NamedRefSerializer(allow_null=True))
    def get_faculty(self, obj):
        return self._named(obj.student_profile.faculty)

    @extend_schema_field(NamedRefSerializer(allow_null=True))
    def get_major(self, obj):
        return self._named(obj.student_profile.major)

    @extend_schema_field(NamedRefSerializer(allow_null=True))
    def get_academic_year(self, obj):
        return self._named(obj.student_profile.academic_year)

    @extend_schema_field(NamedRefSerializer(allow_null=True))
    def get_semester(self, obj):
        return self._named(obj.student_profile.semester)


class DashboardStudentViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """``GET dashboard/students/`` (list only; the per-student screen is ``dashboard/users/<id>/student-detail/``)."""

    permission_classes = [CanViewStudentDetail]
    serializer_class = DashboardStudentSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = StudentFilter

    def get_queryset(self):
        return (
            students_queryset()
            .select_related(
                "student_profile",
                "student_profile__university",
                "student_profile__faculty",
                "student_profile__major",
                "student_profile__academic_year",
                "student_profile__semester",
            )
            .annotate(
                groups_count=count_subquery(_approved_memberships(), "pk", group_field="user_id"),
                pending_enrollments=count_subquery(_pending_enrollments(), "pk", group_field="user_id"),
            )
        )

    @extend_schema(tags=["Dashboard"], responses={200: OpenApiTypes.OBJECT})
    @action(detail=False, methods=["get"], url_path="summary", pagination_class=None, filter_backends=[])
    def summary(self, request):
        """Segment counts for the same filters (``verification_status`` and ``ordering`` are ignored here)."""

        data = request.query_params.copy()
        for ignored in ("verification_status", "ordering"):
            data.pop(ignored, None)
        filterset = StudentFilter(data, queryset=students_queryset(), request=request)
        if not filterset.is_valid():
            raise ValidationError(filterset.errors)
        rows = filterset.qs.order_by().values("student_profile__verification_status").annotate(n=Count("pk")).order_by()
        by_status = {value: 0 for value in StudentVerificationStatus.values}
        for row in rows:
            by_status[row["student_profile__verification_status"]] = row["n"]
        return success_response(
            data={"total": sum(by_status.values()), "by_verification_status": by_status}, request=request
        )
