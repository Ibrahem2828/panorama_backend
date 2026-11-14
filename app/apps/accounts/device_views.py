"""Dashboard: a student's account-device binding (history, switch counter, reset)."""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.views import APIView

from apps.common.responses import success_response

from .devices import DeviceBindingService, binding_enforced, switch_state
from .models import AccountDevice, User
from .permissions import CanManageUsers
from .staff_onboarding import can_manage_staff


class AccountDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = AccountDevice
        fields = [
            "id",
            "name",
            "platform",
            "status",
            "bound_via",
            "first_seen_at",
            "last_seen_at",
            "bound_at",
            "replaced_at",
            "counts_toward_limit",
        ]
        read_only_fields = fields


class AccountDeviceResetSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255, default="")


def _student(request, user_id: int) -> User:
    user = get_object_or_404(User, pk=user_id, is_deleted=False)
    if not can_manage_staff(request.user, user):
        raise PermissionDenied("You cannot manage an account with an equal or higher role.")
    return user


class DashboardAccountDevicesView(APIView):
    """``GET dashboard/users/{user_id}/account-devices/``: the binding history and the self-service switch counter."""

    permission_classes = [CanManageUsers]
    serializer_class = AccountDeviceSerializer

    @extend_schema(tags=["Dashboard"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request, user_id: int):
        user = _student(request, user_id)
        devices = AccountDevice.objects.filter(user=user).order_by("-bound_at", "-id")[:50]
        active = next((d for d in devices if d.status == "active"), None)
        return success_response(
            data={
                "user_id": user.pk,
                "binding_applies": DeviceBindingService.applies_to(user),
                "binding_enforced": binding_enforced(),
                **switch_state(user),
                "active_device": AccountDeviceSerializer(active).data if active else None,
                "devices": AccountDeviceSerializer(devices, many=True).data,
            },
            request=request,
        )


class DashboardAccountDeviceResetView(APIView):
    """``POST dashboard/users/{user_id}/account-devices/reset/``: clears the binding and the switch counter and ends the
    student's sessions; the next login binds the device they use then."""

    permission_classes = [CanManageUsers]
    serializer_class = AccountDeviceResetSerializer

    @extend_schema(tags=["Dashboard"], request=AccountDeviceResetSerializer, responses={200: OpenApiTypes.OBJECT})
    def post(self, request, user_id: int):
        serializer = AccountDeviceResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = _student(request, user_id)
        result = DeviceBindingService.reset(
            user, actor=request.user, reason=serializer.validated_data["reason"], request=request
        )
        return success_response(
            data=result, message="Account device binding was reset", request=request, code="ACCOUNT_DEVICE_RESET"
        )
