from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    DashboardAcademicYearViewSet,
    DashboardFacultyViewSet,
    DashboardMajorViewSet,
    DashboardSemesterViewSet,
    DashboardSubjectViewSet,
    DashboardUniversityViewSet,
    PublicAcademicYearViewSet,
    PublicFacultyByUniversityViewSet,
    PublicMajorByFacultyViewSet,
    PublicSemesterViewSet,
    PublicSubjectByMajorViewSet,
    PublicUniversityViewSet,
)

router = DefaultRouter()
router.register("universities", PublicUniversityViewSet, basename="universities")
router.register("academic-years", PublicAcademicYearViewSet, basename="academic-years")
router.register("semesters", PublicSemesterViewSet, basename="semesters")

dashboard_router = DefaultRouter()
dashboard_router.register("universities", DashboardUniversityViewSet, basename="dashboard-universities")
dashboard_router.register("faculties", DashboardFacultyViewSet, basename="dashboard-faculties")
dashboard_router.register("majors", DashboardMajorViewSet, basename="dashboard-majors")
dashboard_router.register("academic-years", DashboardAcademicYearViewSet, basename="dashboard-academic-years")
dashboard_router.register("semesters", DashboardSemesterViewSet, basename="dashboard-semesters")
dashboard_router.register("subjects", DashboardSubjectViewSet, basename="dashboard-subjects")

urlpatterns = [
    path("", include(router.urls)),
    path(
        "universities/<int:university_pk>/faculties/",
        PublicFacultyByUniversityViewSet.as_view({"get": "list"}),
        name="university-faculties",
    ),
    path(
        "faculties/<int:faculty_pk>/majors/",
        PublicMajorByFacultyViewSet.as_view({"get": "list"}),
        name="faculty-majors",
    ),
    path(
        "majors/<int:major_pk>/subjects/",
        PublicSubjectByMajorViewSet.as_view({"get": "list"}),
        name="major-subjects",
    ),
    path("dashboard/", include(dashboard_router.urls)),
]
