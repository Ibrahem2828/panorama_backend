from django.urls import path

from .dashboard_views import DashboardStatsView
from .health_views import DatabaseHealthCheckView, HealthCheckView

urlpatterns = [
    path("health/", HealthCheckView.as_view(), name="health"),
    path("health/db/", DatabaseHealthCheckView.as_view(), name="health-db"),
    path("dashboard/stats/", DashboardStatsView.as_view(), name="dashboard-stats"),
]
