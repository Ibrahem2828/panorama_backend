from __future__ import annotations

from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiTypes, extend_schema
from rest_framework import filters, permissions, serializers, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.views import APIView

from apps.audit.activity_registry import action_label
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.responses import success_response
from apps.common.throttles import InvitationAcceptThrottle
from apps.common.viewsets import StandardModelViewSet

from .choices import UserRole
from .dashboard_serializers import ALL_CAPABILITIES
from .models import PermissionTemplate, User
from .permissions import CanManageUsers, can_delegate_capability
from .staff_onboarding import (
    STAFF_ROLES,
    InvitationService,
    StaffActivityService,
    can_manage_staff,
    template_warnings,
)


class PermissionTemplateSerializer(serializers.ModelSerializer):
    warnings = serializers.SerializerMethodField()
    confirm_dangerous = serializers.BooleanField(write_only=True, required=False, default=False)

    class Meta:
        model = PermissionTemplate
        fields = [
            "id",
            "key",
            "name_ar",
            "name_en",
            "description",
            "role",
            "capabilities",
            "is_system",
            "is_active",
            "warnings",
            "confirm_dangerous",
        ]
        read_only_fields = ["id", "is_system", "warnings"]

    def get_warnings(self, obj) -> list[str]:
        return template_warnings(obj.capabilities)

    def validate_role(self, value):
        if value not in STAFF_ROLES:
            raise serializers.ValidationError("A template must be for a staff role.")
        return value

    def validate_capabilities(self, value):
        unknown = sorted(set(value) - set(ALL_CAPABILITIES))
        if unknown:
            raise serializers.ValidationError(f"Unknown permissions: {', '.join(unknown)}.")
        return sorted(set(value))

    def validate(self, attrs):
        request = self.context["request"]
        capabilities = attrs.get("capabilities", getattr(self.instance, "capabilities", []))
        for code in capabilities:
            if not can_delegate_capability(request.user, code):
                raise PermissionDenied(f"You cannot grant the permission {code}.")
        role = attrs.get("role", getattr(self.instance, "role", None))
        if role and request.user.role != UserRole.IT_SUPPORT:
            from .permissions import role_rank

            if role_rank(role) >= role_rank(request.user.role):
                raise PermissionDenied("You cannot define a template for an equal or higher role.")
        dangerous = template_warnings(capabilities)
        if dangerous and not attrs.get("confirm_dangerous"):
            raise serializers.ValidationError(
                {
                    "confirm_dangerous": f"This template grants sensitive permissions ({', '.join(dangerous)}). Confirm to continue."
                }
            )
        attrs.pop("confirm_dangerous", None)
        return attrs


class PermissionTemplateViewSet(StandardModelViewSet):
    """Editable permission templates. The five system templates can be edited or switched off, never deleted."""

    permission_classes = [CanManageUsers]
    serializer_class = PermissionTemplateSerializer
    filter_backends = [filters.OrderingFilter]
    ordering = ["id"]

    def get_queryset(self):
        return PermissionTemplate.objects.filter(is_deleted=False)

    def perform_create(self, serializer):
        template = serializer.save()
        self._audit(template, "created")

    def perform_update(self, serializer):
        template = serializer.save()
        self._audit(template, "updated")

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.is_system:
            raise ValidationError({"detail": "System templates cannot be deleted; switch them off instead."})
        response = super().destroy(request, *args, **kwargs)
        self._audit(instance, "deleted")
        return response

    def _audit(self, template, what: str) -> None:
        AuditLogService.log(
            actor=self.request.user,
            action=AuditAction.PERMISSION_TEMPLATE_CHANGED,
            target=template,
            new_value={"what": what, "capabilities": template.capabilities, "role": template.role},
            request=self.request,
            critical=True,
        )


class InviteUserSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=255)
    email = serializers.EmailField()
    phone_number = serializers.CharField(max_length=32)
    template = serializers.PrimaryKeyRelatedField(queryset=PermissionTemplate.objects.filter(is_deleted=False))
    mode = serializers.ChoiceField(choices=["link", "temporary_password"], default="link")


class InviteUserView(APIView):
    permission_classes = [CanManageUsers]

    @extend_schema(tags=["Dashboard"], request=InviteUserSerializer, responses={201: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = InviteUserSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user, delivery = InvitationService.invite(
            actor=request.user,
            full_name=data["full_name"],
            email=data["email"],
            phone_number=data["phone_number"],
            template=data["template"],
            mode=data["mode"],
            request=request,
        )
        sent = delivery["email_sent"]
        return success_response(
            data={
                "id": user.pk,
                "email": user.email,
                "role": user.role,
                "mode": data["mode"],
                "email_sent": sent,
                "email_error": None if sent else delivery["error"],
            },
            message="تم إنشاء الحساب وإرسال الدعوة" if sent else EMAIL_FAILED_MESSAGE,
            status_code=status.HTTP_201_CREATED,
            request=request,
            code="USER_INVITED" if sent else "INVITATION_EMAIL_FAILED",
        )


EMAIL_FAILED_MESSAGE = (
    "تم حفظ الحساب والرابط لكن تعذّر إرسال البريد الإلكتروني. أعد المحاولة لاحقًا أو تحقق من إعدادات البريد."
)


def _target(request, user_id: int) -> User:
    user = get_object_or_404(User, pk=user_id, is_deleted=False)
    if not can_manage_staff(request.user, user):
        raise PermissionDenied("You cannot manage an account with an equal or higher role.")
    return user


class ResendInvitationView(APIView):
    permission_classes = [CanManageUsers]

    @extend_schema(tags=["Dashboard"], request=None, responses={200: OpenApiTypes.OBJECT})
    def post(self, request, user_id: int):
        result = InvitationService.resend(actor=request.user, user=_target(request, user_id), request=request)
        sent = result["email_sent"]
        if not sent:
            message = EMAIL_FAILED_MESSAGE
        elif result["purpose"] == "reset":
            message = "تم إرسال رابط لإعادة تعيين كلمة المرور. كلمة المرور الحالية تبقى صالحة حتى تُحدَّد الجديدة."
        else:
            message = "تم إرسال رابط دعوة جديد."
        return success_response(
            data={
                "purpose": result["purpose"],
                "email_sent": sent,
                "email_error": None if sent else result["error"],
            },
            message=message,
            request=request,
            code="USER_INVITATION_RESENT" if sent else "INVITATION_EMAIL_FAILED",
        )


class RevokeInvitationView(APIView):
    permission_classes = [CanManageUsers]

    @extend_schema(tags=["Dashboard"], request=None, responses={200: OpenApiTypes.OBJECT})
    def post(self, request, user_id: int):
        count = InvitationService.revoke(actor=request.user, user=_target(request, user_id), request=request)
        return success_response(data={"revoked": count}, message="Invitation revoked", request=request)


class UserActivityView(APIView):
    """Last login, live sessions and the user's recent audited actions."""

    permission_classes = [CanManageUsers]

    @extend_schema(tags=["Dashboard"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request, user_id: int):
        user = get_object_or_404(User, pk=user_id, is_deleted=False)
        actions = [
            {
                "id": row.pk,
                "action": row.action,
                "action_label": action_label(row.action, row.new_value),
                "target_type": row.target_type,
                "target_id": row.target_id,
                "ip_address": row.ip_address,
                "created_at": row.created_at,
            }
            for row in StaffActivityService.recent_actions(user)
        ]
        pending = (
            user.invitations.filter(accepted_at__isnull=True, revoked_at__isnull=True).order_by("-created_at").first()
        )
        return success_response(
            data={
                "last_login": user.last_login,
                "last_password_change_at": user.last_password_change_at,
                "active_sessions": StaffActivityService.sessions(user),
                "must_change_password": user.must_change_password,
                "invitation_pending": bool(pending and pending.is_usable),
                "invitation_expires_at": pending.expires_at if pending and pending.is_usable else None,
                "recent_actions": actions,
                "recent_logins": StaffActivityService.recent_logins(user),
                "invitation": StaffActivityService.invitation(user),
            },
            message="User activity",
            request=request,
        )


class EndSessionsView(APIView):
    permission_classes = [CanManageUsers]

    @extend_schema(tags=["Dashboard"], request=None, responses={200: OpenApiTypes.OBJECT})
    def post(self, request, user_id: int):
        with transaction.atomic():
            StaffActivityService.end_sessions(actor=request.user, user=_target(request, user_id), request=request)
        return success_response(message="Sessions ended", request=request, code="USER_SESSIONS_ENDED")


# ---- public: the invited person -----------------------------------------------------------------------------------------------


class InvitationInfoView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [InvitationAcceptThrottle]

    @extend_schema(auth=[], tags=["Auth"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request, token: str):
        """Always 200: ``data.state`` is valid | accepted | expired | revoked | unknown. ``valid`` adds ``full_name``,
        ``expires_at`` and ``purpose`` (invite | reset); ``accepted`` adds ``email_masked`` and ``purpose``."""

        return success_response(data=InvitationService.describe(token), message="Invitation", request=request)


class AcceptInvitationSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=128)
    password = serializers.CharField(write_only=True, max_length=128)
    password_confirm = serializers.CharField(write_only=True, max_length=128)

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError({"password_confirm": "Passwords do not match."})
        return attrs


class AcceptInvitationView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [InvitationAcceptThrottle]

    @extend_schema(auth=[], tags=["Auth"], request=AcceptInvitationSerializer, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = AcceptInvitationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = InvitationService.accept(
            serializer.validated_data["token"], serializer.validated_data["password"], request
        )
        return success_response(
            data={"email": user.email},
            message="Your password is set. You can sign in now.",
            request=request,
            code="INVITATION_ACCEPTED",
        )
