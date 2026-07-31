from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    DashboardNotificationCampaignView,
    DeviceTokenViewSet,
    MarkAllNotificationsReadView,
    MarkNotificationReadView,
    NotificationPreferenceView,
    NotificationViewSet,
    UnreadNotificationCountView,
)

router = DefaultRouter()
router.register("notifications/device-tokens", DeviceTokenViewSet, basename="device-tokens")
router.register("notifications", NotificationViewSet, basename="notifications")

urlpatterns = [
    path("notifications/unread-count/", UnreadNotificationCountView.as_view(), name="notifications-unread-count"),
    path("notifications/<int:pk>/read/", MarkNotificationReadView.as_view(), name="notification-read"),
    path("notifications/read-all/", MarkAllNotificationsReadView.as_view(), name="notifications-read-all"),
    path("notifications/preferences/", NotificationPreferenceView.as_view(), name="notification-preferences"),
    path(
        "dashboard/notifications/campaign/",
        DashboardNotificationCampaignView.as_view(),
        name="dashboard-notification-campaign",
    ),
    *router.urls,
]
