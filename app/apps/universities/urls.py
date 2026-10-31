from django.urls import include, path
from rest_framework.routers import APIRootView, DefaultRouter

from apps.accounts.permissions import CanAccessDashboard

from .student_subjects import StudentSubjectViewSet
from .term_views import (
    CurrentTermView,
    DashboardCurrentTermView,
    EndTermPreviewView,
    EndTermView,
    UndoEndTermView,
    WhatsAppRemovalListView,
)
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
    PublicSubjectByFacultyViewSet,
    PublicSubjectByMajorViewSet,
    PublicUniversityViewSet,
)

router = DefaultRouter()
router.register("universities", PublicUniversityViewSet, basename="universities")
router.register("academic-years", PublicAcademicYearViewSet, basename="academic-years")
router.register("semesters", PublicSemesterViewSet, basename="semesters")
router.register("subjects", StudentSubjectViewSet, basename="student-subjects")


class DashboardAPIRootView(APIRootView):
    """The /dashboard/ index lists admin endpoints, so only dashboard users may see it."""

    permission_classes = [CanAccessDashboard]


class DashboardRouter(DefaultRouter):
    APIRootView = DashboardAPIRootView


dashboard_router = DashboardRouter()
dashboard_router.register("universities", DashboardUniversityViewSet, basename="dashboard-universities")
dashboard_router.register("faculties", DashboardFacultyViewSet, basename="dashboard-faculties")
dashboard_router.register("majors", DashboardMajorViewSet, basename="dashboard-majors")
dashboard_router.register("academic-years", DashboardAcademicYearViewSet, basename="dashboard-academic-years")
dashboard_router.register("semesters", DashboardSemesterViewSet, basename="dashboard-semesters")
dashboard_router.register("subjects", DashboardSubjectViewSet, basename="dashboard-subjects")

urlpatterns = [
    path("academic/current-term/", CurrentTermView.as_view(), name="academic-current-term"),
    path("dashboard/academic/current-term/", DashboardCurrentTermView.as_view(), name="dashboard-current-term"),
    path("dashboard/academic/end-term/preview/", EndTermPreviewView.as_view(), name="dashboard-end-term-preview"),
    path("dashboard/academic/end-term/", EndTermView.as_view(), name="dashboard-end-term"),
    path(
        "dashboard/academic/end-term/<int:rollover_id>/whatsapp-list/",
        WhatsAppRemovalListView.as_view(),
        name="dashboard-end-term-whatsapp-list",
    ),
    path(
        "dashboard/academic/end-term/<int:rollover_id>/undo/",
        UndoEndTermView.as_view(),
        name="dashboard-end-term-undo",
    ),
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
        "faculties/<int:faculty_pk>/subjects/",
        PublicSubjectByFacultyViewSet.as_view({"get": "list"}),
        name="faculty-subjects",
    ),
    path(
        "majors/<int:major_pk>/subjects/",
        PublicSubjectByMajorViewSet.as_view({"get": "list"}),
        name="major-subjects",
    ),
    path("dashboard/", include(dashboard_router.urls)),
]
