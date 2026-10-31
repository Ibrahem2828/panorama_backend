"""Student-facing subject catalogue: every subject of the student's faculty with the semester it belongs to.

Read-only information (name, code, description). Nothing here exposes groups or anything that invites joining: groups
are joined automatically after a subject-registration request is approved.
"""

from __future__ import annotations

from typing import cast

import django_filters
from django.db.models import BooleanField, Case, Exists, OuterRef, Q, Value, When
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import serializers

from apps.accounts.models import User
from apps.accounts.permissions import IsVerifiedStudent
from apps.common.filters import MappedOrderingFilter, StrictBooleanFilter, StrictIntegerFilter
from apps.common.viewsets import StandardReadOnlyModelViewSet
from apps.enrollment.models import EnrollmentItem, EnrollmentItemStatus

from .models import Subject
from .terms import current_semester_id


def _brief(obj) -> dict | None:
    return {"id": obj.pk, "name": obj.name} if obj is not None else None


class StudentSubjectListSerializer(serializers.ModelSerializer):
    academic_year = serializers.SerializerMethodField()
    semester = serializers.SerializerMethodField()
    major = serializers.SerializerMethodField()
    faculty = serializers.SerializerMethodField()
    in_current_term = serializers.BooleanField(read_only=True)
    is_enrolled = serializers.BooleanField(read_only=True)
    has_description = serializers.SerializerMethodField()

    class Meta:
        model = Subject
        fields = [
            "id",
            "name",
            "code",
            "academic_year",
            "semester",
            "major",
            "faculty",
            "in_current_term",
            "is_enrolled",
            "has_description",
        ]
        read_only_fields = fields

    def get_academic_year(self, obj) -> dict:
        year = obj.academic_year
        return {"id": year.pk, "name": year.name, "order": year.order}

    def get_semester(self, obj) -> dict:
        semester = obj.semester
        return {"id": semester.pk, "name": semester.name, "order": semester.order}

    def get_major(self, obj) -> dict | None:
        return _brief(obj.major)

    def get_faculty(self, obj) -> dict:
        return {"id": obj.faculty.pk, "name": obj.faculty.name}

    def get_has_description(self, obj) -> bool:
        return bool((obj.description or "").strip())


class StudentSubjectDetailSerializer(StudentSubjectListSerializer):
    class Meta(StudentSubjectListSerializer.Meta):
        fields = StudentSubjectListSerializer.Meta.fields + ["description"]
        read_only_fields = fields


class StudentSubjectFilter(django_filters.FilterSet):
    academic_year = StrictIntegerFilter(field_name="academic_year_id")
    semester = StrictIntegerFilter(field_name="semester_id")
    search = django_filters.CharFilter(method="filter_search", label="Name or code")
    enrolled = StrictBooleanFilter(field_name="is_enrolled")
    in_current_term = StrictBooleanFilter(field_name="in_current_term")
    ordering = MappedOrderingFilter(
        mapping={
            "name": "name",
            "code": "code",
            "academic_year": "academic_year__order",
            "semester": "semester__order",
            "in_current_term": "in_current_term",
        },
        default="-in_current_term,academic_year,semester,name",
    )

    class Meta:
        model = Subject
        fields: list[str] = []

    def filter_search(self, queryset, name, value):
        term = " ".join((value or "").split())
        if not term:
            return queryset
        return queryset.filter(Q(name__icontains=term) | Q(code__icontains=term))


@extend_schema_view(
    list=extend_schema(
        tags=["Subjects"],
        description="All active subjects of the student's faculty. Filters: `academic_year`, `semester`, `search` "
        "(name or code), `enrolled` (has an approved subject registration), `in_current_term`. Default order: "
        "current-term subjects first, then year, semester, name. Informational only: no group or join data.",
    ),
    retrieve=extend_schema(tags=["Subjects"], description="One subject with its full description."),
)
class StudentSubjectViewSet(StandardReadOnlyModelViewSet):
    permission_classes = [IsVerifiedStudent]
    filter_backends = [DjangoFilterBackend]
    filterset_class = StudentSubjectFilter

    def get_serializer_class(self):
        return StudentSubjectDetailSerializer if self.action == "retrieve" else StudentSubjectListSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Subject.objects.none()
        user = cast(User, self.request.user)
        faculty_id = user.student_profile.faculty_id
        if not faculty_id:
            return Subject.objects.none()
        current = current_semester_id()
        in_term = (
            Case(When(semester_id=current, then=Value(True)), default=Value(False), output_field=BooleanField())
            if current is not None
            else Value(False, output_field=BooleanField())
        )
        approved = EnrollmentItem.objects.filter(
            subject_id=OuterRef("pk"),
            request__user=user,
            request__is_deleted=False,
            status=EnrollmentItemStatus.APPROVED,
            is_deleted=False,
        )
        return (
            Subject.objects.filter(faculty_id=faculty_id, is_active=True, is_deleted=False)
            .select_related("faculty", "major", "academic_year", "semester")
            .annotate(in_current_term=in_term, is_enrolled=Exists(approved))
        )
