"""Staff-side governance of account deletions and mobile devices.

Both screens used to be fail-closed placeholders in the dashboard because the backend only had the mobile self-service
endpoints. Everything here is capability-protected (``users.manage`` for deletions, ``product.manage`` for devices),
audited in the same transaction, and never exposes secrets (push tokens are not part of any response).
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import filters, serializers
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.views import APIView

from apps.accounts.choices import UserRole
from apps.accounts.permissions import CanManageProduct, CanManageUsers, actor_outranks
from apps.audit.models import AuditAction, AuditLog
from apps.audit.services import AuditLogService
from apps.common.responses import success_response
from apps.common.viewsets import StandardReadOnlyModelViewSet

from .models import AccountDeletionRequest, AccountDeletionStatus, DeviceInstallation
from .services import AccountDeletionService

DELETION_ACTIONS = (
    AuditAction.ACCOUNT_DELETION_REQUESTED,
    AuditAction.ACCOUNT_DELETION_CANCELLED,
    AuditAction.ACCOUNT_DELETION_COMPLETED,
)


class _ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(min_length=5, max_length=500, trim_whitespace=True)


class _ExecuteSerializer(_ReasonSerializer):
    confirm_email = serializers.CharField(
        help_text="The e-mail of the account, typed again to confirm an irreversible action."
    )


class DashboardUserBriefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    full_name = serializers.CharField()
    email = serializers.CharField()
    role = serializers.CharField()


class DashboardAccountDeletionSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()
    days_remaining = serializers.SerializerMethodField()

    class Meta:
        model = AccountDeletionRequest
        fields = [
            "id",
            "user",
            "status",
            "requested_at",
            "scheduled_for",
            "cancelled_at",
            "completed_at",
            "reason",
            "days_remaining",
        ]
        read_only_fields = fields

    def get_user(self, obj: AccountDeletionRequest) -> dict:
        return DashboardUserBriefSerializer(obj.user).data

    def get_days_remaining(self, obj: AccountDeletionRequest) -> int | None:
        if obj.status != AccountDeletionStatus.REQUESTED:
            return None
        return max((obj.scheduled_for - timezone.now()).days, 0)


class DashboardAccountDeletionDetailSerializer(DashboardAccountDeletionSerializer):
    timeline = serializers.SerializerMethodField()

    class Meta(DashboardAccountDeletionSerializer.Meta):
        fields = [*DashboardAccountDeletionSerializer.Meta.fields, "timeline"]
        read_only_fields = fields

    def get_timeline(self, obj: AccountDeletionRequest) -> list[dict]:
        rows = AuditLog.objects.filter(
            target_type="AccountDeletionRequest", target_id=str(obj.pk), action__in=DELETION_ACTIONS
        ).select_related("actor")
        return [
            {
                "at": row.created_at,
                "action": row.action,
                "actor": row.actor.full_name if row.actor else None,
                "note": (row.new_value or {}).get("reason") if isinstance(row.new_value, dict) else None,
            }
            for row in rows.order_by("created_at")
        ]


class DashboardAccountDeletionViewSet(StandardReadOnlyModelViewSet):
    """Review and act on account deletion requests (list/detail read-only; cancel and execute are explicit actions)."""

    permission_classes = [CanManageUsers]
    serializer_class = DashboardAccountDeletionSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["user__email", "user__full_name", "user__phone_number", "reason"]
    ordering_fields = ["requested_at", "scheduled_for", "status"]
    ordering = ["-requested_at"]

    def get_queryset(self):
        queryset = AccountDeletionRequest.objects.select_related("user")
        params = self.request.query_params
        if params.get("status"):
            queryset = queryset.filter(status=params["status"])
        if params.get("requested_after"):
            queryset = queryset.filter(requested_at__date__gte=params["requested_after"])
        if params.get("requested_before"):
            queryset = queryset.filter(requested_at__date__lte=params["requested_before"])
        return queryset

    def get_serializer_class(self):
        return (
            DashboardAccountDeletionDetailSerializer
            if self.action == "retrieve"
            else DashboardAccountDeletionSerializer
        )

    @extend_schema(
        tags=["Dashboard"],
        parameters=[
            OpenApiParameter("status", OpenApiTypes.STR, description="requested | cancelled | completed"),
            OpenApiParameter("requested_after", OpenApiTypes.DATE),
            OpenApiParameter("requested_before", OpenApiTypes.DATE),
        ],
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def _guard(self, request, deletion: AccountDeletionRequest) -> None:
        if deletion.user_id == request.user.id:
            raise PermissionDenied("Staff cannot act on their own deletion request.")
        if not actor_outranks(request.user, deletion.user):
            raise PermissionDenied("You may only act on accounts ranked below yours.")

    @extend_schema(
        tags=["Dashboard"], request=_ReasonSerializer, responses={200: DashboardAccountDeletionDetailSerializer}
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Stop a pending deletion (the account stays as it is). A written reason is required and audited."""
        reason = _ReasonSerializer(data=request.data)
        reason.is_valid(raise_exception=True)
        with transaction.atomic():
            deletion = get_object_or_404(
                AccountDeletionRequest.objects.select_for_update().select_related("user"), pk=pk
            )
            self._guard(request, deletion)
            if deletion.status != AccountDeletionStatus.REQUESTED:
                raise ValidationError({"status": "Only a pending deletion request can be cancelled."})
            deletion.status = AccountDeletionStatus.CANCELLED
            deletion.cancelled_at = timezone.now()
            deletion.save(update_fields=["status", "cancelled_at", "updated_at"])
            AuditLogService.log(
                actor=request.user,
                action=AuditAction.ACCOUNT_DELETION_CANCELLED,
                target=deletion,
                new_value={"reason": reason.validated_data["reason"], "by_staff": True},
                request=request,
                critical=True,
            )
        return success_response(
            data=DashboardAccountDeletionDetailSerializer(deletion).data,
            message="Account deletion cancelled",
            request=request,
            code="ACCOUNT_DELETION_CANCELLED_BY_STAFF",
        )

    @extend_schema(
        tags=["Dashboard"], request=_ExecuteSerializer, responses={200: DashboardAccountDeletionDetailSerializer}
    )
    @action(detail=True, methods=["post"])
    def execute(self, request, pk=None):
        """Anonymize the account NOW instead of waiting for the grace period. Irreversible: admins only, reason and the
        account e-mail must be typed again."""
        if request.user.role not in {UserRole.ADMIN, UserRole.IT_SUPPORT}:
            raise PermissionDenied("Only administrators may execute a deletion early.")
        body = _ExecuteSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        deletion = get_object_or_404(AccountDeletionRequest.objects.select_related("user"), pk=pk)
        self._guard(request, deletion)
        if body.validated_data["confirm_email"].strip().lower() != deletion.user.email.lower():
            raise ValidationError({"confirm_email": "The e-mail does not match the account."})
        if not AccountDeletionService.execute(
            deletion.pk, actor=request.user, reason=body.validated_data["reason"], request=request, force=True
        ):
            raise ValidationError({"status": "Only a pending deletion request can be executed."})
        deletion.refresh_from_db()
        return success_response(
            data=DashboardAccountDeletionDetailSerializer(deletion).data,
            message="Account anonymized",
            request=request,
            code="ACCOUNT_DELETION_EXECUTED",
        )


class DashboardDeviceSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()
    is_revoked = serializers.BooleanField(read_only=True)

    class Meta:
        model = DeviceInstallation
        fields = [
            "id",
            "installation_id",
            "user",
            "platform",
            "app_version",
            "build_number",
            "device_model",
            "locale",
            "timezone_name",
            "notifications_enabled",
            "last_seen_at",
            "revoked_at",
            "is_revoked",
            "created_at",
        ]
        read_only_fields = fields  # note: the push token is deliberately NOT exposed

    def get_user(self, obj: DeviceInstallation) -> dict | None:
        return DashboardUserBriefSerializer(obj.user).data if obj.user_id else None


class DashboardDeviceViewSet(StandardReadOnlyModelViewSet):
    permission_classes = [CanManageProduct]
    serializer_class = DashboardDeviceSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["user__email", "user__full_name", "device_model", "app_version"]
    ordering_fields = ["last_seen_at", "created_at", "platform", "app_version"]
    ordering = ["-last_seen_at"]

    def get_queryset(self):
        queryset = DeviceInstallation.objects.filter(is_deleted=False).select_related("user")
        params = self.request.query_params
        if params.get("platform"):
            queryset = queryset.filter(platform=params["platform"])
        if params.get("revoked") in {"true", "1"}:
            queryset = queryset.filter(revoked_at__isnull=False)
        elif params.get("revoked") in {"false", "0"}:
            queryset = queryset.filter(revoked_at__isnull=True)
        if params.get("notifications_enabled") in {"true", "1"}:
            queryset = queryset.filter(notifications_enabled=True)
        elif params.get("notifications_enabled") in {"false", "0"}:
            queryset = queryset.filter(notifications_enabled=False)
        return queryset

    @extend_schema(tags=["Dashboard"], request=_ReasonSerializer, responses={200: DashboardDeviceSerializer})
    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        """Sign a device out of push notifications for good (the installation can register again)."""
        reason = _ReasonSerializer(data=request.data)
        reason.is_valid(raise_exception=True)
        with transaction.atomic():
            device = get_object_or_404(DeviceInstallation.objects.select_for_update(), pk=pk, is_deleted=False)
            if device.revoked_at is not None:
                raise ValidationError({"status": "This device is already revoked."})
            device.revoked_at = timezone.now()
            device.push_token = None
            device.notifications_enabled = False
            device.save(update_fields=["revoked_at", "push_token", "notifications_enabled", "updated_at"])
            AuditLogService.log(
                actor=request.user,
                action=AuditAction.DEVICE_INSTALLATION_REVOKED,
                target=device,
                new_value={"reason": reason.validated_data["reason"], "by_staff": True},
                request=request,
                critical=True,
            )
        return success_response(
            data=DashboardDeviceSerializer(device).data,
            message="Device revoked",
            request=request,
            code="DEVICE_REVOKED",
        )


class DashboardDeviceStatsView(APIView):
    permission_classes = [CanManageProduct]

    @extend_schema(tags=["Dashboard"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        queryset = DeviceInstallation.objects.filter(is_deleted=False)
        totals = queryset.aggregate(
            total=Count("id"),
            active=Count("id", filter=Q(revoked_at__isnull=True)),
            revoked=Count("id", filter=Q(revoked_at__isnull=False)),
            push_enabled=Count("id", filter=Q(revoked_at__isnull=True, notifications_enabled=True)),
        )
        by_platform = list(queryset.filter(revoked_at__isnull=True).values("platform").annotate(count=Count("id")))
        by_version = list(
            queryset.filter(revoked_at__isnull=True)
            .values("app_version")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )
        return success_response(
            data={**totals, "by_platform": by_platform, "top_versions": by_version},
            request=request,
            code="DEVICE_STATS",
        )
