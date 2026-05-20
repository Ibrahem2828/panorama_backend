from django.utils import timezone
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import filters, status
from rest_framework.views import APIView

from apps.common.responses import success_response
from apps.common.viewsets import StandardModelViewSet, StandardReadOnlyModelViewSet

from .models import DeviceToken, Notification
from .serializers import DeviceTokenSerializer, NotificationSerializer


class NotificationViewSet(StandardReadOnlyModelViewSet):
    serializer_class = NotificationSerializer
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ["created_at", "is_read"]
    ordering = ["-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Notification.objects.none()
        return Notification.objects.filter(user=self.request.user, is_deleted=False)


class UnreadNotificationCountView(APIView):
    serializer_class = NotificationSerializer

    @extend_schema(tags=["Notifications"])
    def get(self, request):
        count = Notification.objects.filter(user=request.user, is_read=False, is_deleted=False).count()
        return success_response(data={"count": count})


class MarkNotificationReadView(APIView):
    serializer_class = NotificationSerializer

    @extend_schema(tags=["Notifications"])
    def post(self, request, pk: int):
        notification = get_object_or_404(Notification, pk=pk, user=request.user, is_deleted=False)
        notification.mark_read()
        return success_response(data=NotificationSerializer(notification).data, message="Notification marked as read")


class MarkAllNotificationsReadView(APIView):
    serializer_class = NotificationSerializer

    @extend_schema(tags=["Notifications"])
    def post(self, request):
        now = timezone.now()
        Notification.objects.filter(user=request.user, is_read=False, is_deleted=False).update(
            is_read=True,
            read_at=now,
            updated_at=now,
        )
        return success_response(message="All notifications marked as read", status_code=status.HTTP_200_OK)


class DeviceTokenViewSet(StandardModelViewSet):
    serializer_class = DeviceTokenSerializer
    http_method_names = ["post", "delete"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return DeviceToken.objects.none()
        return DeviceToken.objects.filter(user=self.request.user, is_deleted=False)
