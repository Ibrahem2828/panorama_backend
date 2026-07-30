from __future__ import annotations

from django.db import transaction
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiTypes, extend_schema
from rest_framework import filters
from rest_framework.views import APIView

from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.responses import success_response
from apps.common.viewsets import StandardReadOnlyModelViewSet

from .dashboard_serializers import (
    ALL_CAPABILITIES,
    DashboardUserSerializer,
    PermissionOverrideUpsertSerializer,
    UserPermissionOverrideSerializer,
)
from .models import User, UserPermissionOverride
from .permissions import CanManageUsers


class DashboardUserViewSet(StandardReadOnlyModelViewSet):
    permission_classes = [CanManageUsers]
    serializer_class = DashboardUserSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["role", "is_active", "is_email_verified", "is_phone_verified"]
    search_fields = ["full_name", "email", "phone_number", "username"]
    ordering_fields = ["date_joined", "full_name", "role", "last_login"]
    ordering = ["-date_joined"]

    def get_queryset(self):
        return User.objects.filter(is_deleted=False).prefetch_related("permission_overrides")

    def partial_update(self, request, pk=None):
        user = get_object_or_404(self.get_queryset(), pk=pk)
        old = {"role": user.role, "is_active": user.is_active, "full_name": user.full_name}
        serializer = self.serializer_class(user, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        new = {"role": user.role, "is_active": user.is_active, "full_name": user.full_name}
        action = AuditAction.USER_ROLE_CHANGED if old["role"] != new["role"] else AuditAction.USER_STATUS_CHANGED
        AuditLogService.log(actor=request.user, action=action, target=user, old_value=old, new_value=new, request=request)
        return success_response(data=serializer.data, message="User updated", request=request, code="DASHBOARD_USER_UPDATED")


class DashboardCapabilitiesView(APIView):
    permission_classes = [CanManageUsers]

    @extend_schema(tags=["Dashboard"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return success_response(data={"capabilities": ALL_CAPABILITIES}, request=request)


class DashboardUserPermissionOverridesView(APIView):
    permission_classes = [CanManageUsers]

    @extend_schema(tags=["Dashboard"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request, user_id: int):
        user = get_object_or_404(User, pk=user_id, is_deleted=False)
        items = user.permission_overrides.filter(is_deleted=False).order_by("permission_code")
        return success_response(data=UserPermissionOverrideSerializer(items, many=True).data, request=request)

    @transaction.atomic
    @extend_schema(tags=["Dashboard"], request=PermissionOverrideUpsertSerializer, responses={200: OpenApiTypes.OBJECT})
    def put(self, request, user_id: int):
        user = get_object_or_404(User.objects.select_for_update(), pk=user_id, is_deleted=False)
        serializer = PermissionOverrideUpsertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        code = serializer.validated_data["permission_code"]
        old = UserPermissionOverride.objects.filter(user=user, permission_code=code, is_deleted=False).first()
        override, _ = UserPermissionOverride.objects.update_or_create(
            user=user,
            permission_code=code,
            defaults={
                "effect": serializer.validated_data["effect"],
                "expires_at": serializer.validated_data.get("expires_at"),
                "reason": serializer.validated_data.get("reason", ""),
                "granted_by": request.user,
                "is_deleted": False,
                "deleted_at": None,
            },
        )
        AuditLogService.log(
            actor=request.user, action=AuditAction.USER_PERMISSION_OVERRIDE_CHANGED, target=user,
            old_value=UserPermissionOverrideSerializer(old).data if old else None,
            new_value=UserPermissionOverrideSerializer(override).data, request=request,
        )
        return success_response(
            data=UserPermissionOverrideSerializer(override).data,
            message="Permission override saved", request=request, code="PERMISSION_OVERRIDE_SAVED",
        )

    @transaction.atomic
    @extend_schema(tags=["Dashboard"], request=OpenApiTypes.OBJECT, responses={200: OpenApiTypes.OBJECT})
    def delete(self, request, user_id: int):
        user = get_object_or_404(User, pk=user_id, is_deleted=False)
        code = request.data.get("permission_code", "")
        override = get_object_or_404(UserPermissionOverride, user=user, permission_code=code, is_deleted=False)
        old = UserPermissionOverrideSerializer(override).data
        override.delete()
        AuditLogService.log(
            actor=request.user, action=AuditAction.USER_PERMISSION_OVERRIDE_CHANGED, target=user,
            old_value=old, new_value={"removed": code}, request=request,
        )
        return success_response(message="Permission override removed", request=request, code="PERMISSION_OVERRIDE_REMOVED")
