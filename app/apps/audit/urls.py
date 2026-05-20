from rest_framework.routers import DefaultRouter

from .views import DashboardAuditLogViewSet

router = DefaultRouter()
router.register("dashboard/audit-logs", DashboardAuditLogViewSet, basename="dashboard-audit-logs")

urlpatterns = router.urls
