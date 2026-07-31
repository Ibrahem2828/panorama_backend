from __future__ import annotations

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import status
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import CanManageProduct
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.responses import success_response
from apps.common.throttles import DeviceRegistrationRateThrottle
from apps.common.viewsets import StandardModelViewSet

from .models import (
    AccountDeletionRequest,
    DeviceInstallation,
    FeatureFlag,
    MaintenanceMode,
    MobileAppReleasePolicy,
    PrivacyPolicyVersion,
    TermsVersion,
    UserConsent,
)
from .serializers import (
    AccountDeletionRequestSerializer,
    DeviceInstallationSerializer,
    FeatureFlagSerializer,
    MaintenanceModeSerializer,
    MobileAppReleasePolicySerializer,
    PolicyAcceptanceSerializer,
    PrivacyPolicyVersionSerializer,
    TermsVersionSerializer,
    UserConsentSerializer,
)
from .services import (
    AccountDeletionService,
    FeatureFlagService,
    IdempotencyService,
    ProductConfigurationService,
)


class IdempotencyConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "A request with this Idempotency-Key is already in progress."
    default_code = "idempotency_in_progress"


def _idempotency(request, endpoint: str):
    try:
        decision = IdempotencyService.begin(request, endpoint=endpoint)
    except ValueError as exc:
        raise ValidationError({"Idempotency-Key": str(exc)}) from exc
    except RuntimeError as exc:
        raise IdempotencyConflict() from exc
    if decision.replay_body is not None and decision.replay_status is not None:
        return decision, Response(decision.replay_body, status=decision.replay_status)
    return decision, None


class MobileBootstrapView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        tags=["Mobile"],
        parameters=[
            OpenApiParameter("X-App-Platform", OpenApiTypes.STR, OpenApiParameter.HEADER, required=True),
            OpenApiParameter("X-App-Version", OpenApiTypes.STR, OpenApiParameter.HEADER, required=False),
            OpenApiParameter("X-App-Build", OpenApiTypes.INT, OpenApiParameter.HEADER, required=False),
            OpenApiParameter("X-Installation-ID", OpenApiTypes.UUID, OpenApiParameter.HEADER, required=False),
            OpenApiParameter("X-Device-Locale", OpenApiTypes.STR, OpenApiParameter.HEADER, required=False),
        ],
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request):
        platform = request.headers.get("X-App-Platform", "").strip().lower()
        if platform not in {"android", "ios"}:
            raise ValidationError({"X-App-Platform": "Use android or ios."})
        policy = ProductConfigurationService.active_release_policy(platform)
        maintenance = ProductConfigurationService.active_maintenance()
        active_terms = TermsVersion.objects.filter(is_active=True, is_deleted=False).order_by("-published_at").first()
        active_privacy = (
            PrivacyPolicyVersion.objects.filter(is_active=True, is_deleted=False).order_by("-published_at").first()
        )
        update = {
            "minimum_supported_version": policy.minimum_supported_version if policy else "",
            "minimum_supported_build": policy.minimum_supported_build if policy else 0,
            "latest_version": policy.latest_version if policy else "",
            "latest_build": policy.latest_build if policy else 0,
            "mode": policy.update_mode if policy else "none",
            "store_url": policy.store_url if policy else "",
            "message_ar": policy.message_ar if policy else "",
            "message_en": policy.message_en if policy else "",
            "grace_period_ends_at": policy.grace_period_ends_at if policy else None,
        }
        return success_response(
            data={
                "server_time": timezone.now(),
                "api_version": "v1",
                "platform": platform,
                "update": update,
                "maintenance": {
                    "enabled": bool(maintenance),
                    "title_ar": maintenance.title_ar if maintenance else "",
                    "title_en": maintenance.title_en if maintenance else "",
                    "message_ar": maintenance.message_ar if maintenance else "",
                    "message_en": maintenance.message_en if maintenance else "",
                    "retry_after_seconds": maintenance.retry_after_seconds if maintenance else None,
                },
                "feature_flags": FeatureFlagService.public_flags(platform=platform),
                "support": {"email": "", "url": ""},
                "policies": {
                    "terms": TermsVersionSerializer(active_terms).data if active_terms else None,
                    "privacy": PrivacyPolicyVersionSerializer(active_privacy).data if active_privacy else None,
                },
            },
            request=request,
            code="MOBILE_BOOTSTRAP",
        )


class MobileUpdatePolicyView(MobileBootstrapView):
    """Compatibility-friendly lightweight public update-policy endpoint."""


class DeviceRegistrationView(APIView):
    throttle_classes = [DeviceRegistrationRateThrottle]
    serializer_class = DeviceInstallationSerializer

    @extend_schema(tags=["Mobile"], request=DeviceInstallationSerializer, responses={200: DeviceInstallationSerializer})
    def post(self, request):
        decision, replay = _idempotency(request, "mobile-device-register")
        if replay:
            return replay
        serializer = DeviceInstallationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data
        installation_id = validated["installation_id"]
        with transaction.atomic():
            device = DeviceInstallation.objects.select_for_update().filter(installation_id=installation_id).first()
            if device and device.user_id and device.user_id != request.user.id:
                raise PermissionDenied("This installation belongs to another account.")
            push_token = validated.get("push_token")
            if push_token:
                token_owner = (
                    DeviceInstallation.objects.select_for_update()
                    .filter(push_token=push_token)
                    .exclude(installation_id=installation_id)
                    .first()
                )
                if token_owner:
                    raise ValidationError(
                        {"push_token": "This push token is already registered to another installation."}
                    )
            defaults = {**validated, "user": request.user, "last_seen_at": timezone.now(), "revoked_at": None}
            device, created = DeviceInstallation.objects.update_or_create(
                installation_id=installation_id, defaults=defaults
            )
        AuditLogService.log(
            actor=request.user,
            action=AuditAction.DEVICE_INSTALLATION_REGISTERED,
            target=device,
            new_value={"platform": device.platform, "created": created},
            request=request,
        )
        response = success_response(
            data=DeviceInstallationSerializer(device).data,
            status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
            request=request,
            code="DEVICE_REGISTERED",
        )
        IdempotencyService.complete(decision, response)
        return response


class DeviceDetailView(APIView):
    serializer_class = DeviceInstallationSerializer

    def _device(self, request, installation_id):
        return get_object_or_404(
            DeviceInstallation, installation_id=installation_id, user=request.user, is_deleted=False
        )

    @extend_schema(tags=["Mobile"], request=DeviceInstallationSerializer, responses={200: DeviceInstallationSerializer})
    def patch(self, request, installation_id):
        device = self._device(request, installation_id)
        if device.revoked_at:
            raise ValidationError({"installation_id": "The installation has been revoked."})
        serializer = DeviceInstallationSerializer(device, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(last_seen_at=timezone.now())
        return success_response(data=serializer.data, request=request, code="DEVICE_UPDATED")


class DeviceRevokeView(APIView):
    @extend_schema(tags=["Mobile"], request=None, responses={200: OpenApiTypes.OBJECT})
    def post(self, request, installation_id):
        device = get_object_or_404(
            DeviceInstallation, installation_id=installation_id, user=request.user, is_deleted=False
        )
        device.revoked_at = timezone.now()
        device.notifications_enabled = False
        device.push_token = None
        device.save(update_fields=["revoked_at", "notifications_enabled", "push_token", "updated_at"])
        AuditLogService.log(
            actor=request.user, action=AuditAction.DEVICE_INSTALLATION_REVOKED, target=device, request=request
        )
        return success_response(message="Device installation revoked", request=request, code="DEVICE_REVOKED")


class CurrentPoliciesView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(tags=["Policies"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        terms = TermsVersion.objects.filter(is_active=True, is_deleted=False).order_by("-published_at").first()
        privacy = (
            PrivacyPolicyVersion.objects.filter(is_active=True, is_deleted=False).order_by("-published_at").first()
        )
        return success_response(
            data={
                "terms": TermsVersionSerializer(terms).data if terms else None,
                "privacy": PrivacyPolicyVersionSerializer(privacy).data if privacy else None,
            },
            request=request,
            code="CURRENT_POLICIES",
        )


class PolicyAcceptanceView(APIView):
    serializer_class = PolicyAcceptanceSerializer

    @extend_schema(
        tags=["Policies"], request=PolicyAcceptanceSerializer, responses={200: UserConsentSerializer(many=True)}
    )
    def post(self, request):
        decision, replay = _idempotency(request, "policy-acceptance")
        if replay:
            return replay
        serializer = PolicyAcceptanceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        accepted = []
        locale = serializer.validated_data["locale"]
        for field, model, kind in (
            ("terms_version", TermsVersion, "terms"),
            ("privacy_version", PrivacyPolicyVersion, "privacy"),
        ):
            version = serializer.validated_data.get(field)
            if not version:
                continue
            if not model.objects.filter(version=version, is_active=True, is_deleted=False).exists():
                raise ValidationError({field: "This policy version is not active."})
            consent, _ = UserConsent.objects.get_or_create(
                user=request.user, kind=kind, version=version, defaults={"locale": locale}
            )
            accepted.append(consent)
        AuditLogService.log(
            actor=request.user,
            action=AuditAction.POLICY_ACCEPTED,
            new_value={"kinds": [consent.kind for consent in accepted]},
            request=request,
        )
        response = success_response(
            data=UserConsentSerializer(accepted, many=True).data,
            request=request,
            code="POLICIES_ACCEPTED",
        )
        IdempotencyService.complete(decision, response)
        return response


class AccountDeletionRequestView(APIView):
    serializer_class = AccountDeletionRequestSerializer

    @extend_schema(
        tags=["Account"], request=AccountDeletionRequestSerializer, responses={202: AccountDeletionRequestSerializer}
    )
    def post(self, request):
        if not FeatureFlagService.is_enabled("account_deletion_enabled", role=request.user.role):
            raise PermissionDenied("Account deletion is not currently available.")
        decision, replay = _idempotency(request, "account-deletion-request")
        if replay:
            return replay
        serializer = AccountDeletionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        deletion = AccountDeletionService.request(
            request.user, reason=serializer.validated_data.get("reason", ""), request=request
        )
        response = success_response(
            data=AccountDeletionRequestSerializer(deletion).data,
            status_code=status.HTTP_202_ACCEPTED,
            request=request,
            code="ACCOUNT_DELETION_REQUESTED",
        )
        IdempotencyService.complete(decision, response)
        return response


class AccountDeletionCancelView(APIView):
    @extend_schema(tags=["Account"], request=None, responses={200: AccountDeletionRequestSerializer})
    def post(self, request):
        try:
            deletion = AccountDeletionService.cancel(request.user, request=request)
        except AccountDeletionRequest.DoesNotExist as exc:
            raise ValidationError({"detail": "No deletion request exists."}) from exc
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return success_response(
            data=AccountDeletionRequestSerializer(deletion).data,
            request=request,
            code="ACCOUNT_DELETION_CANCELLED",
        )


class AccountDeletionStatusView(APIView):
    @extend_schema(tags=["Account"], responses={200: AccountDeletionRequestSerializer})
    def get(self, request):
        deletion = AccountDeletionRequest.objects.filter(user=request.user).first()
        return success_response(
            data=AccountDeletionRequestSerializer(deletion).data if deletion else None,
            request=request,
            code="ACCOUNT_DELETION_STATUS",
        )


class DashboardProductViewSet(StandardModelViewSet):
    permission_classes = [CanManageProduct]

    audit_action = ""

    def perform_create(self, serializer):
        model_fields = {field.name for field in serializer.Meta.model._meta.fields}
        instance = serializer.save(**({"updated_by": self.request.user} if "updated_by" in model_fields else {}))
        AuditLogService.log(actor=self.request.user, action=self.audit_action, target=instance, request=self.request)

    def perform_update(self, serializer):
        model_fields = {field.name for field in serializer.Meta.model._meta.fields}
        instance = serializer.save(**({"updated_by": self.request.user} if "updated_by" in model_fields else {}))
        AuditLogService.log(actor=self.request.user, action=self.audit_action, target=instance, request=self.request)


class DashboardMobileReleasePolicyViewSet(DashboardProductViewSet):
    serializer_class = MobileAppReleasePolicySerializer
    audit_action = AuditAction.MOBILE_RELEASE_POLICY_UPDATED

    def get_queryset(self):
        return MobileAppReleasePolicy.objects.filter(is_deleted=False)


class DashboardMaintenanceModeViewSet(DashboardProductViewSet):
    serializer_class = MaintenanceModeSerializer
    audit_action = AuditAction.MAINTENANCE_MODE_UPDATED

    def get_queryset(self):
        return MaintenanceMode.objects.filter(is_deleted=False)


class DashboardFeatureFlagViewSet(DashboardProductViewSet):
    serializer_class = FeatureFlagSerializer
    audit_action = AuditAction.FEATURE_FLAG_UPDATED

    def get_queryset(self):
        return FeatureFlag.objects.filter(is_deleted=False)


class DashboardTermsVersionViewSet(DashboardProductViewSet):
    serializer_class = TermsVersionSerializer
    audit_action = AuditAction.POLICY_ACCEPTED

    def get_queryset(self):
        return TermsVersion.objects.filter(is_deleted=False)


class DashboardPrivacyPolicyVersionViewSet(DashboardProductViewSet):
    serializer_class = PrivacyPolicyVersionSerializer
    audit_action = AuditAction.POLICY_ACCEPTED

    def get_queryset(self):
        return PrivacyPolicyVersion.objects.filter(is_deleted=False)
