from rest_framework.routers import DefaultRouter

from .views import DashboardAuditLogViewSet, DashboardSystemActivityViewSet

router = DefaultRouter()
router.register("dashboard/audit-logs", DashboardAuditLogViewSet, basename="dashboard-audit-logs")
router.register("dashboard/system-activity", DashboardSystemActivityViewSet, basename="dashboard-system-activity")

urlpatterns = router.urls
