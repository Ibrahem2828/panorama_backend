from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import filters, permissions

from apps.accounts.permissions import IsAdminOrITSupport
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
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
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


class PublicSubjectByMajorViewSet(AcademicQuerysetMixin, StandardReadOnlyModelViewSet):
    serializer_class = SubjectSerializer
    permission_classes = [permissions.AllowAny]
    filterset_fields = ["academic_year", "semester"]
    search_fields = ["name", "code", "description"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Subject.objects.none()
        return Subject.objects.filter(
            major_id=self.kwargs["major_pk"],
            major__is_active=True,
            academic_year__is_active=True,
            semester__is_active=True,
            is_active=True,
            is_deleted=False,
        ).select_related("major", "academic_year", "semester")


class DashboardAcademicMixin(AcademicQuerysetMixin, StandardModelViewSet):
    permission_classes = [IsAdminOrITSupport]
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


class DashboardSubjectViewSet(DashboardAcademicMixin):
    queryset = Subject.objects.filter(is_deleted=False).select_related("major", "academic_year", "semester")
    serializer_class = SubjectSerializer
    filterset_fields = ["major", "academic_year", "semester", "is_active"]
    search_fields = ["name", "code", "description"]
