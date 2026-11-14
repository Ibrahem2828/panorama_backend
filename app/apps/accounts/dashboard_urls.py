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
from .device_views import DashboardAccountDeviceResetView, DashboardAccountDevicesView
from .phone_views import (
    DashboardPhoneVerificationClaimView,
    DashboardPhoneVerificationMarkSentView,
    DashboardPhoneVerificationRejectView,
    DashboardPhoneVerificationViewSet,
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
router.register(
    "dashboard/phone-verifications", DashboardPhoneVerificationViewSet, basename="dashboard-phone-verifications"
)
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
    path(
        "dashboard/users/<int:user_id>/account-devices/",
        DashboardAccountDevicesView.as_view(),
        name="dashboard-user-account-devices",
    ),
    path(
        "dashboard/users/<int:user_id>/account-devices/reset/",
        DashboardAccountDeviceResetView.as_view(),
        name="dashboard-user-account-devices-reset",
    ),
    path(
        "dashboard/phone-verifications/<int:pk>/claim/",
        DashboardPhoneVerificationClaimView.as_view(),
        name="dashboard-phone-verification-claim",
    ),
    path(
        "dashboard/phone-verifications/<int:pk>/mark-sent/",
        DashboardPhoneVerificationMarkSentView.as_view(),
        name="dashboard-phone-verification-mark-sent",
    ),
    path(
        "dashboard/phone-verifications/<int:pk>/reject/",
        DashboardPhoneVerificationRejectView.as_view(),
        name="dashboard-phone-verification-reject",
    ),
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
