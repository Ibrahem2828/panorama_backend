from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    DeviceTokenViewSet,
    MarkAllNotificationsReadView,
    MarkNotificationReadView,
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
    *router.urls,
]
