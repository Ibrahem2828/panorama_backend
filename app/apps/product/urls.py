from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    AccountDeletionCancelView,
    AccountDeletionRequestView,
    AccountDeletionStatusView,
    CurrentPoliciesView,
    DashboardFeatureFlagViewSet,
    DashboardMaintenanceModeViewSet,
    DashboardMobileReleasePolicyViewSet,
    DashboardPrivacyPolicyVersionViewSet,
    DashboardTermsVersionViewSet,
    DeviceDetailView,
    DeviceRegistrationView,
    DeviceRevokeView,
    MobileBootstrapView,
    MobileUpdatePolicyView,
    PolicyAcceptanceView,
)

router = DefaultRouter()
router.register(
    "dashboard/mobile-release-policies",
    DashboardMobileReleasePolicyViewSet,
    basename="dashboard-mobile-release-policies",
)
router.register("dashboard/maintenance-modes", DashboardMaintenanceModeViewSet, basename="dashboard-maintenance-modes")
router.register("dashboard/feature-flags", DashboardFeatureFlagViewSet, basename="dashboard-feature-flags")
router.register("dashboard/terms-versions", DashboardTermsVersionViewSet, basename="dashboard-terms-versions")
router.register(
    "dashboard/privacy-policy-versions",
    DashboardPrivacyPolicyVersionViewSet,
    basename="dashboard-privacy-policy-versions",
)

urlpatterns = [
    path("mobile/bootstrap/", MobileBootstrapView.as_view(), name="mobile-bootstrap"),
    path("mobile/update-policy/", MobileUpdatePolicyView.as_view(), name="mobile-update-policy"),
    path("mobile/devices/register/", DeviceRegistrationView.as_view(), name="mobile-device-register"),
    path("mobile/devices/<uuid:installation_id>/", DeviceDetailView.as_view(), name="mobile-device-detail"),
    path("mobile/devices/<uuid:installation_id>/revoke/", DeviceRevokeView.as_view(), name="mobile-device-revoke"),
    path("policies/current/", CurrentPoliciesView.as_view(), name="policies-current"),
    path("policies/accept/", PolicyAcceptanceView.as_view(), name="policies-accept"),
    path("account/deletion/request/", AccountDeletionRequestView.as_view(), name="account-deletion-request"),
    path("account/deletion/cancel/", AccountDeletionCancelView.as_view(), name="account-deletion-cancel"),
    path("account/deletion/status/", AccountDeletionStatusView.as_view(), name="account-deletion-status"),
    *router.urls,
]
