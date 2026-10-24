from django.urls import path
from rest_framework.routers import DefaultRouter

from .dashboard_views import NotificationHealthView, QuickAccessView
from .views import (
    DashboardNotificationCampaignView,
    DeviceTokenViewSet,
    MarkAllNotificationsReadView,
    MarkNotificationReadView,
    NotificationPreferenceView,
    NotificationSummaryView,
    NotificationViewSet,
    UnreadNotificationCountView,
    WebPushPublicKeyView,
    WebPushSubscriptionView,
)

router = DefaultRouter()
router.register("notifications/device-tokens", DeviceTokenViewSet, basename="device-tokens")
router.register("notifications", NotificationViewSet, basename="notifications")

urlpatterns = [
    path("notifications/unread-count/", UnreadNotificationCountView.as_view(), name="notifications-unread-count"),
    path("notifications/summary/", NotificationSummaryView.as_view(), name="notifications-summary"),
    path("notifications/<int:pk>/read/", MarkNotificationReadView.as_view(), name="notification-read"),
    path("notifications/read-all/", MarkAllNotificationsReadView.as_view(), name="notifications-read-all"),
    path("notifications/web-push/public-key/", WebPushPublicKeyView.as_view(), name="web-push-public-key"),
    path("notifications/web-push/subscriptions/", WebPushSubscriptionView.as_view(), name="web-push-subscriptions"),
    path("notifications/preferences/", NotificationPreferenceView.as_view(), name="notification-preferences"),
    path("dashboard/quick-access/", QuickAccessView.as_view(), name="dashboard-quick-access"),
    path("dashboard/notifications/health/", NotificationHealthView.as_view(), name="dashboard-notification-health"),
    path(
        "dashboard/notifications/campaign/",
        DashboardNotificationCampaignView.as_view(),
        name="dashboard-notification-campaign",
    ),
    *router.urls,
]
