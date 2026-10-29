from django.urls import path
from rest_framework.routers import DefaultRouter

from .dashboard_students import DashboardStudentViewSet
from .dashboard_views import (
    DashboardCapabilitiesView,
    DashboardStudentDetailView,
    DashboardUserPermissionOverrideDetailView,
    DashboardUserPermissionOverridesView,
    DashboardUserViewSet,
)
from .staff_views import (
    EndSessionsView,
    InviteUserView,
    PermissionTemplateViewSet,
    ResendInvitationView,
    RevokeInvitationView,
    UserActivityView,
)

router = DefaultRouter()
router.register("dashboard/users", DashboardUserViewSet, basename="dashboard-users")
router.register("dashboard/students", DashboardStudentViewSet, basename="dashboard-students")
router.register("dashboard/permission-templates", PermissionTemplateViewSet, basename="dashboard-permission-templates")

urlpatterns = [
    path("dashboard/users/invite/", InviteUserView.as_view(), name="dashboard-user-invite"),
    path(
        "dashboard/users/<int:user_id>/resend-invitation/",
        ResendInvitationView.as_view(),
        name="dashboard-user-resend-invitation",
    ),
    path(
        "dashboard/users/<int:user_id>/revoke-invitation/",
        RevokeInvitationView.as_view(),
        name="dashboard-user-revoke-invitation",
    ),
    path(
        "dashboard/users/<int:user_id>/student-detail/",
        DashboardStudentDetailView.as_view(),
        name="dashboard-user-student-detail",
    ),
    path("dashboard/users/<int:user_id>/activity/", UserActivityView.as_view(), name="dashboard-user-activity"),
    path("dashboard/users/<int:user_id>/end-sessions/", EndSessionsView.as_view(), name="dashboard-user-end-sessions"),
    *router.urls,
    path("dashboard/capabilities/", DashboardCapabilitiesView.as_view(), name="dashboard-capabilities"),
    path(
        "dashboard/users/<int:user_id>/permission-overrides/",
        DashboardUserPermissionOverridesView.as_view(),
        name="dashboard-user-permission-overrides",
    ),
    path(
        "dashboard/users/<int:user_id>/permission-overrides/<str:permission_code>/",
        DashboardUserPermissionOverrideDetailView.as_view(),
        name="dashboard-user-permission-override-detail",
    ),
]
