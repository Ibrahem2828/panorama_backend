from django.urls import path
from rest_framework.routers import DefaultRouter

from .dashboard_views import (
    DashboardCapabilitiesView, DashboardUserPermissionOverridesView, DashboardUserViewSet,
)

router = DefaultRouter()
router.register("dashboard/users", DashboardUserViewSet, basename="dashboard-users")

urlpatterns = [
    *router.urls,
    path("dashboard/capabilities/", DashboardCapabilitiesView.as_view(), name="dashboard-capabilities"),
    path(
        "dashboard/users/<int:user_id>/permission-overrides/",
        DashboardUserPermissionOverridesView.as_view(), name="dashboard-user-permission-overrides",
    ),
]
