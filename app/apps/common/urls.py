from django.urls import path

from .dashboard_views import DashboardStatsView
from .health_views import (
    DatabaseHealthCheckView,
    HealthCheckView,
    LivenessHealthCheckView,
    ReadinessHealthCheckView,
    StartupHealthCheckView,
)

urlpatterns = [
    path("health/", HealthCheckView.as_view(), name="health"),
    path("health/live/", LivenessHealthCheckView.as_view(), name="health-live"),
    path("health/db/", DatabaseHealthCheckView.as_view(), name="health-db"),
    path("health/ready/", ReadinessHealthCheckView.as_view(), name="health-ready"),
    path("health/startup/", StartupHealthCheckView.as_view(), name="health-startup"),
    path("dashboard/stats/", DashboardStatsView.as_view(), name="dashboard-stats"),
]
