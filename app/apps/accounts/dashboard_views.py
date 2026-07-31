from __future__ import annotations

from django.db import transaction
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiTypes, extend_schema
from rest_framework import filters, status
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.responses import success_response
from apps.common.viewsets import StandardExplicitActionViewSet
from apps.product.services import IdempotencyService

from .choices import UserRole
from .dashboard_serializers import (
    ALL_CAPABILITIES,
    DashboardDeactivateUserSerializer,
    DashboardUserSerializer,
    DashboardUserUpdateSerializer,
    PermissionOverrideUpsertSerializer,
    UserPermissionOverrideSerializer,
)
from .models import User, UserPermissionOverride
from .permissions import CanManageUsers


class IdempotencyConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "A request with this Idempotency-Key is already in progress."
    default_code = "idempotency_in_progress"


class DashboardUserViewSet(StandardExplicitActionViewSet):
    """Read users, PATCH an allowed profile subset, and use explicit status actions."""

    permission_classes = [CanManageUsers]
    serializer_class = DashboardUserSerializer
    http_method_names = ["get", "patch", "post", "head", "options"]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["role", "is_active", "is_email_verified", "is_phone_verified"]
    search_fields = ["full_name", "email", "phone_number", "username"]
    ordering_fields = ["date_joined", "full_name", "role", "last_login"]
    ordering = ["-date_joined"]

    def get_queryset(self):
        return User.objects.filter(is_deleted=False).prefetch_related("permission_overrides")

    def get_serializer_class(self):
        if self.action == "partial_update":
            return DashboardUserUpdateSerializer
        return DashboardUserSerializer

    @staticmethod
    def _idempotency(request, endpoint: str):
        try:
            decision = IdempotencyService.begin(request, endpoint=endpoint, required=True)
        except ValueError as exc:
            raise ValidationError({"Idempotency-Key": str(exc)}) from exc
        except RuntimeError as exc:
            raise IdempotencyConflict() from exc
        if decision.replay_body is not None and decision.replay_status is not None:
            return decision, Response(decision.replay_body, status=decision.replay_status)
        return decision, None

    def partial_update(self, request, pk=None):
        user = self.get_object()
        old = {"role": user.role, "is_active": user.is_active, "full_name": user.full_name}
        serializer = self.get_serializer(user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        if old["role"] != user.role:
            user.invalidate_sessions()
        new = {"role": user.role, "is_active": user.is_active, "full_name": user.full_name}
        audit_action = AuditAction.USER_ROLE_CHANGED if old["role"] != new["role"] else AuditAction.USER_STATUS_CHANGED
        AuditLogService.log(
            actor=request.user,
            action=audit_action,
            target=user,
            old_value=old,
            new_value=new,
            request=request,
        )
        return success_response(
            data=DashboardUserSerializer(user, context={"request": request}).data,
            message="User updated",
            request=request,
            code="DASHBOARD_USER_UPDATED",
        )

    @action(detail=True, methods=["post"])
    @extend_schema(tags=["Dashboard"], request=None, responses={200: DashboardUserSerializer})
    def activate(self, request, pk=None):
        decision, replay = self._idempotency(request, "dashboard-user-activate")
        if replay:
            return replay
        with transaction.atomic():
            user = get_object_or_404(User.objects.select_for_update(), pk=pk, is_deleted=False)
            was_active = user.is_active
            if not was_active:
                user.is_active = True
                user.save(update_fields=["is_active", "updated_at"])
                user.invalidate_sessions()
        AuditLogService.log(
            actor=request.user,
            action=AuditAction.USER_STATUS_CHANGED,
            target=user,
            old_value={"is_active": was_active},
            new_value={"is_active": True},
            request=request,
        )
        response = success_response(
            data=DashboardUserSerializer(user, context={"request": request}).data,
            message="User activated",
            request=request,
            code="DASHBOARD_USER_ACTIVATED",
        )
        IdempotencyService.complete(decision, response)
        return response

    @action(detail=True, methods=["post"])
    @extend_schema(
        tags=["Dashboard"], request=DashboardDeactivateUserSerializer, responses={200: DashboardUserSerializer}
    )
    def deactivate(self, request, pk=None):
        serializer = DashboardDeactivateUserSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        decision, replay = self._idempotency(request, "dashboard-user-deactivate")
        if replay:
            return replay
        with transaction.atomic():
            user = get_object_or_404(User.objects.select_for_update(), pk=pk, is_deleted=False)
            if user.pk == request.user.pk:
                raise PermissionDenied("You cannot deactivate your own account.")
            if user.role == UserRole.IT_SUPPORT and user.is_active:
                has_another_critical_account = (
                    User.objects.filter(role=UserRole.IT_SUPPORT, is_active=True, is_deleted=False)
                    .exclude(pk=user.pk)
                    .exists()
                )
                if not has_another_critical_account:
                    raise ValidationError({"detail": "The system must keep one active IT Support account."})
            was_active = user.is_active
            if was_active:
                user.is_active = False
                user.save(update_fields=["is_active", "updated_at"])
                user.invalidate_sessions()
        AuditLogService.log(
            actor=request.user,
            action=AuditAction.USER_STATUS_CHANGED,
            target=user,
            old_value={"is_active": was_active},
            new_value={"is_active": False, "reason": serializer.validated_data["reason"]},
            request=request,
        )
        response = success_response(
            data=DashboardUserSerializer(user, context={"request": request}).data,
            message="User deactivated",
            request=request,
            code="DASHBOARD_USER_DEACTIVATED",
        )
        IdempotencyService.complete(decision, response)
        return response


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
            actor=request.user,
            action=AuditAction.USER_PERMISSION_OVERRIDE_CHANGED,
            target=user,
            old_value=UserPermissionOverrideSerializer(old).data if old else None,
            new_value=UserPermissionOverrideSerializer(override).data,
            request=request,
        )
        return success_response(
            data=UserPermissionOverrideSerializer(override).data,
            message="Permission override saved",
            request=request,
            code="PERMISSION_OVERRIDE_SAVED",
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
            actor=request.user,
            action=AuditAction.USER_PERMISSION_OVERRIDE_CHANGED,
            target=user,
            old_value=old,
            new_value={"removed": code},
            request=request,
        )
        return success_response(
            message="Permission override removed",
            request=request,
            code="PERMISSION_OVERRIDE_REMOVED",
        )
