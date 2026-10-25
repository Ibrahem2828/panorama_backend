"""Staff onboarding: permission templates, invitations (link or temporary password), session control and activity."""

from __future__ import annotations

import hashlib
import logging
import secrets
import string
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError

from apps.audit.models import AuditAction, AuditLog
from apps.audit.services import AuditLogService

from .choices import PermissionEffect, UserRole
from .models import PermissionTemplate, User, UserInvitation, UserPermissionOverride
from .permissions import (
    ROLE_CAPABILITIES,
    Capability,
    PermissionService,
    can_delegate_capability,
    role_rank,
)

logger = logging.getLogger(__name__)

#: Capabilities whose misuse is costly; a template that grants one needs an explicit confirmation.
DANGEROUS_CAPABILITIES = frozenset(
    {Capability.SYSTEM_MANAGE, Capability.USERS_MANAGE, Capability.FINANCE_MANAGE, Capability.AUDIT_VIEW}
)
STAFF_ROLES = frozenset(
    {UserRole.ADMIN, UserRole.PRINT_STAFF, UserRole.SUPPORT_STAFF, UserRole.CONTENT_MANAGER, UserRole.IT_SUPPORT}
)


def role_defaults(role: str) -> set[str]:
    return set(ROLE_CAPABILITIES.get(UserRole(role), set()))


def template_warnings(capabilities) -> list[str]:
    return sorted(set(capabilities) & DANGEROUS_CAPABILITIES)


class InvitationError(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "This invitation link is invalid or has expired."
    default_code = "invitation_invalid"


class TemplateService:
    @staticmethod
    def assert_actor_may_use(actor, template: PermissionTemplate) -> None:
        """An actor can only create accounts below their own rank and can only hand out capabilities they hold."""

        if actor.role != UserRole.IT_SUPPORT and role_rank(template.role) >= role_rank(actor.role):
            raise PermissionDenied("You cannot create an account with an equal or higher role.")
        for code in template.capabilities:
            if not can_delegate_capability(actor, code):
                raise PermissionDenied(f"You cannot grant the permission {code}.")

    @staticmethod
    @transaction.atomic
    def apply(user: User, template: PermissionTemplate, actor) -> None:
        """Set the role and make the user's effective capabilities exactly the template's (via overrides on top of the role)."""

        user.role = template.role
        user.is_staff = False
        user.save(update_fields=["role", "is_staff", "updated_at"])
        UserPermissionOverride.objects.filter(user=user).delete()
        wanted = set(template.capabilities)
        base = role_defaults(template.role)
        if "*" in base:
            return  # IT Support already holds everything
        reason = f"template:{template.key}"[:255]
        for code in sorted(wanted - base):
            UserPermissionOverride.objects.create(
                user=user, permission_code=code, effect=PermissionEffect.ALLOW, granted_by=actor, reason=reason
            )
        for code in sorted(base - wanted):
            UserPermissionOverride.objects.create(
                user=user, permission_code=code, effect=PermissionEffect.DENY, granted_by=actor, reason=reason
            )


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def temporary_password() -> str:
    """Readable, random, and certain to pass the password validators (letters, digits and a symbol)."""

    alphabet = string.ascii_letters + string.digits
    body = "".join(secrets.choice(alphabet) for _ in range(12))
    return f"{body}!{secrets.choice(string.digits)}a"


class InvitationService:
    @staticmethod
    def link(token: str) -> str:
        return f"{settings.DASHBOARD_BASE_URL}/ar/invite/{token}"

    @classmethod
    def _issue(cls, user: User, actor, template: PermissionTemplate | None) -> tuple[UserInvitation, str]:
        UserInvitation.objects.filter(user=user, accepted_at__isnull=True, revoked_at__isnull=True).update(
            revoked_at=timezone.now()
        )
        token = secrets.token_urlsafe(32)
        invitation = UserInvitation.objects.create(
            user=user,
            token_hash=_hash(token),
            expires_at=timezone.now() + timedelta(hours=settings.INVITATION_TTL_HOURS),
            created_by=actor,
            template=template,
        )
        return invitation, token

    @staticmethod
    def _send(email: str, subject: str, body: str) -> None:
        try:
            send_mail(
                subject=subject,
                message=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=False,
            )
        except Exception:  # noqa: BLE001 - the account exists; staff can resend the invitation
            logger.exception("invitation_email_failed")

    @classmethod
    @transaction.atomic
    def invite(
        cls,
        *,
        actor,
        full_name: str,
        email: str,
        phone_number: str,
        template: PermissionTemplate,
        mode: str,
        request=None,
    ) -> tuple[User, str | None]:
        """Create the staff account and e-mail how to get in. Returns the user and (temporary-password mode only) nothing else:
        secrets are never returned to the caller, they only travel by e-mail."""

        TemplateService.assert_actor_may_use(actor, template)
        if not template.is_active:
            raise ValidationError({"template": "This template is switched off."})
        email = email.strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError({"email": "An account with this e-mail already exists."})
        if User.objects.filter(phone_number=phone_number.strip()).exists():
            raise ValidationError({"phone_number": "An account with this phone number already exists."})
        user = User.objects.create_user(
            email=email,
            phone_number=phone_number,
            full_name=full_name.strip(),
            password=None,
            role=template.role,
            is_email_verified=True,  # the address is proven by the invitation itself
        )
        user.set_unusable_password()
        user.save(update_fields=["password", "updated_at"])
        TemplateService.apply(user, template, actor)
        subject = "دعوة للانضمام إلى لوحة تحكم بانوراما"
        if mode == "temporary_password":
            password = temporary_password()
            user.set_password(password)
            user.must_change_password = True
            user.save(update_fields=["password", "must_change_password", "updated_at"])
            body = (
                f"مرحبًا {user.full_name}،\n\nأُنشئ لك حساب في لوحة تحكم بانوراما.\n"
                f"البريد: {user.email}\nكلمة المرور المؤقتة: {password}\n"
                f"سجّل الدخول من {settings.DASHBOARD_BASE_URL} وسيُطلب منك اختيار كلمة مرور جديدة فورًا."
            )
            transaction.on_commit(lambda: cls._send(user.email, subject, body))
        else:
            invitation, token = cls._issue(user, actor, template)
            body = (
                f"مرحبًا {user.full_name}،\n\nدُعيت للانضمام إلى لوحة تحكم بانوراما.\n"
                f"افتح الرابط التالي لتحديد كلمة مرورك (صالح {settings.INVITATION_TTL_HOURS} ساعة ولمرة واحدة):\n"
                f"{cls.link(token)}"
            )
            transaction.on_commit(lambda: cls._send(user.email, subject, body))
        AuditLogService.log(
            actor=actor,
            action=AuditAction.USER_INVITED,
            target=user,
            new_value={"template": template.key, "role": template.role, "mode": mode},
            request=request,
            critical=True,
        )
        return user, None

    @classmethod
    @transaction.atomic
    def resend(cls, *, actor, user: User, request=None) -> None:
        if user.has_usable_password() and not user.must_change_password:
            raise ValidationError({"detail": "This account has already been set up."})
        if (
            user.role != UserRole.IT_SUPPORT
            and actor.role != UserRole.IT_SUPPORT
            and role_rank(user.role) >= role_rank(actor.role)
        ):
            raise PermissionDenied("You cannot manage an account with an equal or higher role.")
        previous = user.invitations.order_by("-created_at").first()
        invitation, token = cls._issue(user, actor, previous.template if previous else None)
        user.set_unusable_password()
        user.must_change_password = False
        user.save(update_fields=["password", "must_change_password", "updated_at"])
        body = (
            f"مرحبًا {user.full_name}،\n\nهذا رابط جديد لتحديد كلمة مرورك في لوحة تحكم بانوراما "
            f"(صالح {settings.INVITATION_TTL_HOURS} ساعة ولمرة واحدة):\n{cls.link(token)}"
        )
        transaction.on_commit(lambda: cls._send(user.email, "رابط جديد للانضمام إلى بانوراما", body))
        AuditLogService.log(
            actor=actor,
            action=AuditAction.USER_INVITED,
            target=user,
            new_value={"resent": True},
            request=request,
            critical=True,
        )
        del invitation

    @staticmethod
    @transaction.atomic
    def revoke(*, actor, user: User, request=None) -> int:
        count = UserInvitation.objects.filter(user=user, accepted_at__isnull=True, revoked_at__isnull=True).update(
            revoked_at=timezone.now()
        )
        AuditLogService.log(
            actor=actor,
            action=AuditAction.USER_INVITATION_REVOKED,
            target=user,
            new_value={"revoked": count},
            request=request,
            critical=True,
        )
        return count

    @staticmethod
    def lookup(token: str) -> UserInvitation | None:
        if not token or len(token) > 128:
            return None
        invitation = (
            UserInvitation.objects.select_related("user").filter(token_hash=_hash(token), is_deleted=False).first()
        )
        return invitation if invitation and invitation.is_usable and invitation.user.is_active else None

    @classmethod
    @transaction.atomic
    def accept(cls, token: str, password: str, request=None) -> User:
        invitation = cls.lookup(token)
        if invitation is None:
            raise InvitationError()
        locked = UserInvitation.objects.select_for_update().get(pk=invitation.pk)
        if not locked.is_usable:  # lost a race with a second submit of the same link
            raise InvitationError()
        user = locked.user
        try:
            validate_password(password, user)
        except Exception as exc:  # noqa: BLE001 - Django's ValidationError carries the readable messages
            raise ValidationError({"password": list(getattr(exc, "messages", [str(exc)]))}) from exc
        user.set_password(password)
        user.last_password_change_at = timezone.now()
        user.must_change_password = False
        user.is_email_verified = True
        user.save(
            update_fields=[
                "password",
                "last_password_change_at",
                "must_change_password",
                "is_email_verified",
                "updated_at",
            ]
        )
        locked.accepted_at = timezone.now()
        locked.save(update_fields=["accepted_at", "updated_at"])
        AuditLogService.log(
            actor=user, action=AuditAction.USER_INVITATION_ACCEPTED, target=user, request=request, critical=True
        )
        return user


class StaffActivityService:
    @staticmethod
    def sessions(user: User) -> int:
        from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

        live = OutstandingToken.objects.filter(user=user, expires_at__gt=timezone.now())
        return live.exclude(pk__in=BlacklistedToken.objects.values("token_id")).count()

    @staticmethod
    def recent_actions(user: User, limit: int = 50):
        return AuditLog.objects.filter(actor=user).order_by("-created_at", "-id")[:limit]

    @staticmethod
    @transaction.atomic
    def end_sessions(*, actor, user: User, request=None) -> None:
        user.invalidate_sessions()
        AuditLogService.log(
            actor=actor, action=AuditAction.USER_SESSIONS_ENDED, target=user, request=request, critical=True
        )


def can_manage_staff(actor, target: User) -> bool:
    return actor.role == UserRole.IT_SUPPORT or role_rank(target.role) < role_rank(actor.role)


__all__ = [
    "DANGEROUS_CAPABILITIES",
    "InvitationError",
    "InvitationService",
    "PermissionService",
    "StaffActivityService",
    "TemplateService",
    "can_manage_staff",
    "template_warnings",
]
