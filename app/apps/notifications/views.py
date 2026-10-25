from typing import cast

from django.conf import settings
from django.db import models, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiTypes, extend_schema
from rest_framework import filters, status
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.accounts.permissions import CanManageProduct
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.responses import error_response, success_response
from apps.common.throttles import WebPushRateThrottle
from apps.common.viewsets import StandardModelViewSet, StandardReadOnlyModelViewSet

from .models import DeviceToken, Notification, NotificationPreference, WebPushSubscription
from .serializers import (
    DeviceTokenSerializer,
    NotificationCampaignSerializer,
    NotificationPreferenceSerializer,
    NotificationSerializer,
    WebPushPublicKeySerializer,
    WebPushSubscriptionSerializer,
    WebPushUnsubscribeSerializer,
)
from .services import NotificationService, web_push_configured


def _current_user(request) -> User:
    """Default DRF authentication guarantees a user at these private endpoints."""

    return cast(User, request.user)


def _visible_notifications(user: User):
    """Notifications a user can see: not deleted and not expired. Shared by the list and the unread count."""

    return Notification.objects.filter(user=user, is_deleted=False).filter(
        models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=timezone.now())
    )


class NotificationViewSet(StandardReadOnlyModelViewSet):
    serializer_class = NotificationSerializer
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ["created_at", "is_read"]
    ordering = ["-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Notification.objects.none()
        queryset = _visible_notifications(_current_user(self.request))
        params = self.request.query_params
        if params.get("category"):
            queryset = queryset.filter(category=params["category"])
        if params.get("is_read") in {"true", "false", "1", "0"}:
            queryset = queryset.filter(is_read=params["is_read"] in {"true", "1"})
        return queryset


class UnreadNotificationCountView(APIView):
    serializer_class = NotificationSerializer

    @extend_schema(tags=["Notifications"])
    def get(self, request):
        # Same predicate as the list: an expired notification is neither listed nor counted.
        count = _visible_notifications(_current_user(request)).filter(is_read=False).count()
        return success_response(data={"count": count})


class NotificationSummaryView(APIView):
    """``GET /notifications/summary/``: unread count in total and per category (the bell and the inbox tabs)."""

    serializer_class = NotificationSerializer

    @extend_schema(tags=["Notifications"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        unread = _visible_notifications(_current_user(request)).filter(is_read=False)
        by_category = {
            (row["category"] or "general"): row["n"] for row in unread.values("category").annotate(n=models.Count("id"))
        }
        return success_response(data={"unread": sum(by_category.values()), "by_category": by_category}, request=request)


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
        queryset = Notification.objects.filter(user=_current_user(request), is_read=False, is_deleted=False)
        if request.query_params.get("category"):
            queryset = queryset.filter(category=request.query_params["category"])
        queryset.update(
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


class WebPushPublicKeyView(APIView):
    """The VAPID application server key the browser needs for ``pushManager.subscribe``."""

    serializer_class = WebPushPublicKeySerializer

    @extend_schema(tags=["Notifications"], responses={200: WebPushPublicKeySerializer})
    def get(self, request):
        enabled = web_push_configured()
        return success_response(
            data={"enabled": enabled, "public_key": settings.WEB_PUSH_VAPID_PUBLIC_KEY if enabled else None},
            request=request,
        )


class WebPushSubscriptionView(APIView):
    """Register (upsert by endpoint) or remove the calling user's browser push subscription."""

    serializer_class = WebPushSubscriptionSerializer
    throttle_classes = [WebPushRateThrottle]

    @extend_schema(
        tags=["Notifications"], request=WebPushSubscriptionSerializer, responses={201: WebPushSubscriptionSerializer}
    )
    def post(self, request):
        if not web_push_configured():
            return error_response(
                "Browser push notifications are not available.",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="WEB_PUSH_NOT_CONFIGURED",
                request=request,
            )
        serializer = WebPushSubscriptionSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        subscription = serializer.save()
        return success_response(
            data=WebPushSubscriptionSerializer(subscription, context={"request": request}).data,
            message="Browser push subscription saved",
            status_code=status.HTTP_201_CREATED,
            request=request,
            code="WEB_PUSH_SUBSCRIBED",
        )

    @extend_schema(tags=["Notifications"], request=WebPushUnsubscribeSerializer, responses={200: OpenApiTypes.OBJECT})
    def delete(self, request):
        serializer = WebPushUnsubscribeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        subscription = get_object_or_404(
            WebPushSubscription,
            endpoint=serializer.validated_data["endpoint"].strip(),
            user=_current_user(request),
            is_deleted=False,
        )
        subscription.is_deleted = True
        subscription.is_active = False
        subscription.save(update_fields=["is_deleted", "is_active", "updated_at"])
        return success_response(
            message="Browser push subscription removed", request=request, code="WEB_PUSH_UNSUBSCRIBED"
        )


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
        with transaction.atomic():
            created = NotificationService.create_bulk_notifications(
                users,
                serializer.validated_data["title"],
                serializer.validated_data["body"],
                serializer.validated_data["type"],
                {"deep_link": serializer.validated_data.get("deep_link", "")},
            )
            # Critical: a campaign that cannot be recorded is rolled back (push tasks only fire on commit).
            AuditLogService.log(
                actor=request.user,
                action=AuditAction.NOTIFICATION_CAMPAIGN_CREATED,
                new_value={
                    "type": serializer.validated_data["type"],
                    "recipient_count": len(users),
                    "created_count": len(created),
                },
                request=request,
                critical=True,
            )
        return success_response(
            data={"created_count": len(created)}, status_code=status.HTTP_201_CREATED, request=request
        )
