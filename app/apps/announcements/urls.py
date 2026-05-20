from rest_framework.routers import DefaultRouter

from .views import AnnouncementViewSet, DashboardAnnouncementViewSet

router = DefaultRouter()
router.register("announcements", AnnouncementViewSet, basename="announcements")
router.register("dashboard/announcements", DashboardAnnouncementViewSet, basename="dashboard-announcements")

urlpatterns = router.urls
