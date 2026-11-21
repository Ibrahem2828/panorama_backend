from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import AnnouncementImageView, AnnouncementViewSet, DashboardAnnouncementViewSet

router = DefaultRouter()
router.register("announcements", AnnouncementViewSet, basename="announcements")
router.register("dashboard/announcements", DashboardAnnouncementViewSet, basename="dashboard-announcements")

urlpatterns = [
    path("announcements/<int:pk>/image/", AnnouncementImageView.as_view(), name="announcement-image"),
    *router.urls,
]
