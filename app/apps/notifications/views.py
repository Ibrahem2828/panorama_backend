from typing import cast

from django.db import models
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiTypes, extend_schema
from rest_framework import filters, status
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.accounts.permissions import CanManageProduct
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.responses import success_response
from apps.common.viewsets import StandardModelViewSet, StandardReadOnlyModelViewSet

from .models import DeviceToken, Notification, NotificationPreference
from .serializers import (
    DeviceTokenSerializer,
    NotificationCampaignSerializer,
    NotificationPreferenceSerializer,
    NotificationSerializer,
)
from .services import NotificationService


def _current_user(request) -> User:
    """Default DRF authentication guarantees a user at these private endpoints."""

    return cast(User, request.user)


class NotificationViewSet(StandardReadOnlyModelViewSet):
    serializer_class = NotificationSerializer
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ["created_at", "is_read"]
    ordering = ["-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Notification.objects.none()
        return Notification.objects.filter(user=_current_user(self.request), is_deleted=False).filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=timezone.now())
        )


class UnreadNotificationCountView(APIView):
    serializer_class = NotificationSerializer

    @extend_schema(tags=["Notifications"])
    def get(self, request):
        count = Notification.objects.filter(user=_current_user(request), is_read=False, is_deleted=False).count()
        return success_response(data={"count": count})


class MarkNotificationReadView(APIView):
    serializer_class = NotificationSerializer

    @extend_schema(tags=["Notifications"])
    def post(self, request, pk: int):
        notification = get_object_or_404(Notification, pk=pk, user=_current_user(request), is_deleted=False)
        notification.mark_read()
        return success_response(data=NotificationSerializer(notification).data, message="Notification marked as read")


class MarkAllNotificationsReadView(APIView):
    serializer_class = NotificationSerializer

    @extend_schema(tags=["Notifications"])
    def post(self, request):
        now = timezone.now()
        Notification.objects.filter(user=_current_user(request), is_read=False, is_deleted=False).update(
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
        return DeviceToken.objects.filter(user=_current_user(self.request), is_deleted=False)


class NotificationPreferenceView(APIView):
    serializer_class = NotificationPreferenceSerializer

    def _preference(self, request):
        preference, _ = NotificationPreference.objects.get_or_create(user=_current_user(request))
        return preference

    @extend_schema(tags=["Notifications"], responses={200: NotificationPreferenceSerializer})
    def get(self, request):
        return success_response(data=NotificationPreferenceSerializer(self._preference(request)).data, request=request)

    @extend_schema(
        tags=["Notifications"],
        request=NotificationPreferenceSerializer,
        responses={200: NotificationPreferenceSerializer},
    )
    def patch(self, request):
        serializer = NotificationPreferenceSerializer(self._preference(request), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return success_response(data=serializer.data, request=request, code="NOTIFICATION_PREFERENCES_UPDATED")


class DashboardNotificationCampaignView(APIView):
    permission_classes = [CanManageProduct]
    serializer_class = NotificationCampaignSerializer

    @extend_schema(
        tags=["Dashboard notifications"], request=NotificationCampaignSerializer, responses={201: OpenApiTypes.OBJECT}
    )
    def post(self, request):
        serializer = NotificationCampaignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        users = list(
            User.objects.filter(id__in=serializer.validated_data["user_ids"], is_active=True, is_deleted=False)
        )
        created = NotificationService.create_bulk_notifications(
            users,
            serializer.validated_data["title"],
            serializer.validated_data["body"],
            serializer.validated_data["type"],
            {"deep_link": serializer.validated_data.get("deep_link", "")},
        )
        AuditLogService.log(
            actor=request.user,
            action=AuditAction.NOTIFICATION_CAMPAIGN_CREATED,
            new_value={"recipient_count": len(users), "created_count": len(created)},
            request=request,
        )
        return success_response(
            data={"created_count": len(created)}, status_code=status.HTTP_201_CREATED, request=request
        )
