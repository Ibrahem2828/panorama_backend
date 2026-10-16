from typing import Any

import django_filters
from django.db.models import Q
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import filters, permissions
from rest_framework.exceptions import ValidationError

from apps.accounts.permissions import CanManageAcademic
from apps.audit.mixins import AuditedModelWriteMixin
from apps.audit.models import AuditAction
from apps.common.viewsets import StandardModelViewSet, StandardReadOnlyModelViewSet

from .models import AcademicYear, Faculty, Major, Semester, Subject, University
from .serializers import (
    AcademicYearSerializer,
    FacultySerializer,
    MajorSerializer,
    SemesterSerializer,
    SubjectSerializer,
    UniversitySerializer,
)


class AcademicQuerysetMixin:
    filter_backends: Any = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    search_fields = ["name", "code"]
    ordering_fields = ["id", "name", "code", "created_at", "updated_at", "order"]
    ordering = ["id"]


@extend_schema_view(list=extend_schema(tags=["Universities"]), retrieve=extend_schema(tags=["Universities"]))
class PublicUniversityViewSet(AcademicQuerysetMixin, StandardReadOnlyModelViewSet):
    serializer_class = UniversitySerializer
    permission_classes = [permissions.AllowAny]
    search_fields = ["name", "code", "description"]

    def get_queryset(self):
        return University.objects.filter(is_active=True, is_deleted=False)


class PublicFacultyByUniversityViewSet(AcademicQuerysetMixin, StandardReadOnlyModelViewSet):
    serializer_class = FacultySerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Faculty.objects.none()
        return Faculty.objects.filter(
            university_id=self.kwargs["university_pk"],
            university__is_active=True,
            is_active=True,
            is_deleted=False,
        ).select_related("university")


class PublicMajorByFacultyViewSet(AcademicQuerysetMixin, StandardReadOnlyModelViewSet):
    serializer_class = MajorSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Major.objects.none()
        return Major.objects.filter(
            faculty_id=self.kwargs["faculty_pk"],
            faculty__is_active=True,
            is_active=True,
            is_deleted=False,
        ).select_related("faculty", "faculty__university")


class PublicAcademicYearViewSet(AcademicQuerysetMixin, StandardReadOnlyModelViewSet):
    serializer_class = AcademicYearSerializer
    permission_classes = [permissions.AllowAny]
    search_fields = ["name"]
    filterset_fields = ["is_active"]

    def get_queryset(self):
        return AcademicYear.objects.filter(is_active=True, is_deleted=False)


class PublicSemesterViewSet(AcademicQuerysetMixin, StandardReadOnlyModelViewSet):
    serializer_class = SemesterSerializer
    permission_classes = [permissions.AllowAny]
    search_fields = ["name"]
    filterset_fields = ["is_active"]

    def get_queryset(self):
        return Semester.objects.filter(is_active=True, is_deleted=False)


def _public_subjects():
    return Subject.objects.filter(
        faculty__is_active=True,
        academic_year__is_active=True,
        semester__is_active=True,
        is_active=True,
        is_deleted=False,
    ).select_related("faculty", "faculty__university", "major", "major__faculty", "academic_year", "semester")


def _tagged_or_faculty_level(queryset, major_id):
    """Subjects of that active major plus the faculty-level ones (no specialization)."""

    return queryset.filter(Q(major__isnull=True) | Q(major_id=major_id, major__is_active=True))


class PublicSubjectByFacultyViewSet(AcademicQuerysetMixin, StandardReadOnlyModelViewSet):
    """Subjects of a faculty. ``?major=<id>`` narrows to that major's subjects plus the faculty-level ones."""

    serializer_class = SubjectSerializer
    permission_classes = [permissions.AllowAny]
    filterset_fields = ["academic_year", "semester"]
    search_fields = ["name", "code", "description"]

    @extend_schema(
        tags=["Universities"],
        parameters=[
            OpenApiParameter("major", OpenApiTypes.INT, description="Only faculty-level subjects and this major's."),
        ],
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Subject.objects.none()
        queryset = _public_subjects().filter(faculty_id=self.kwargs["faculty_pk"])
        major = self.request.query_params.get("major")
        if major:
            try:
                major_id = int(major)
            except ValueError:
                raise ValidationError({"major": "A valid integer is required."}) from None
            queryset = _tagged_or_faculty_level(queryset, major_id)
        else:
            queryset = queryset.filter(Q(major__isnull=True) | Q(major__is_active=True))
        return queryset


class PublicSubjectByMajorViewSet(AcademicQuerysetMixin, StandardReadOnlyModelViewSet):
    """Backward-compatible: the major's subjects plus the faculty-level subjects of the same faculty."""

    serializer_class = SubjectSerializer
    permission_classes = [permissions.AllowAny]
    filterset_fields = ["academic_year", "semester"]
    search_fields = ["name", "code", "description"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Subject.objects.none()
        major = Major.objects.filter(pk=self.kwargs["major_pk"], is_active=True, is_deleted=False).first()
        if major is None:
            return Subject.objects.none()
        return _tagged_or_faculty_level(_public_subjects().filter(faculty_id=major.faculty_id), major.pk)


class DashboardAcademicMixin(AuditedModelWriteMixin, AcademicQuerysetMixin, StandardModelViewSet):
    permission_classes = [CanManageAcademic]
    audit_create_action = AuditAction.ACADEMIC_ENTITY_CREATED
    audit_update_action = AuditAction.ACADEMIC_ENTITY_UPDATED
    audit_delete_action = AuditAction.ACADEMIC_ENTITY_DELETED
    filterset_fields = ["is_active"]


@extend_schema_view(
    list=extend_schema(tags=["Dashboard"]),
    create=extend_schema(tags=["Dashboard"]),
    retrieve=extend_schema(tags=["Dashboard"]),
    partial_update=extend_schema(tags=["Dashboard"]),
    destroy=extend_schema(tags=["Dashboard"]),
)
class DashboardUniversityViewSet(DashboardAcademicMixin):
    queryset = University.objects.filter(is_deleted=False)
    serializer_class = UniversitySerializer
    search_fields = ["name", "code", "description"]


class DashboardFacultyViewSet(DashboardAcademicMixin):
    queryset = Faculty.objects.filter(is_deleted=False).select_related("university")
    serializer_class = FacultySerializer
    filterset_fields = ["university", "is_active"]


class DashboardMajorViewSet(DashboardAcademicMixin):
    queryset = Major.objects.filter(is_deleted=False).select_related("faculty", "faculty__university")
    serializer_class = MajorSerializer
    filterset_fields = ["faculty", "faculty__university", "is_active"]


class DashboardAcademicYearViewSet(DashboardAcademicMixin):
    queryset = AcademicYear.objects.filter(is_deleted=False)
    serializer_class = AcademicYearSerializer
    search_fields = ["name"]


class DashboardSemesterViewSet(DashboardAcademicMixin):
    queryset = Semester.objects.filter(is_deleted=False)
    serializer_class = SemesterSerializer
    search_fields = ["name"]


class DashboardSubjectFilter(django_filters.FilterSet):
    has_major = django_filters.BooleanFilter(field_name="major", lookup_expr="isnull", exclude=True)

    class Meta:
        model = Subject
        fields = ["faculty", "major", "has_major", "academic_year", "semester", "is_active"]


class DashboardSubjectViewSet(DashboardAcademicMixin):
    queryset = Subject.objects.filter(is_deleted=False).select_related(
        "faculty", "faculty__university", "major", "major__faculty", "academic_year", "semester"
    )
    serializer_class = SubjectSerializer
    filterset_class = DashboardSubjectFilter
    search_fields = ["name", "code", "description"]
