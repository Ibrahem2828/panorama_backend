"""Staff onboarding: permission templates, invitations (link or temporary password), session control and activity."""

from __future__ import annotations

import hashlib
import logging
import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timedelta

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


class CodedError(APIException):
    """A refusal with a stable machine-readable ``code`` and a readable Arabic message (HTTP 400 by default)."""

    status_code: int = status.HTTP_400_BAD_REQUEST

    def __init__(self, code: str, message: str, status_code: int | None = None):
        super().__init__(detail=message, code=code.lower())
        self.api_code = code
        if status_code is not None:
            self.status_code = status_code


INVITATION_MESSAGES = {
    "INVITATION_ACCEPTED_ALREADY": "تم تعيين كلمة المرور بالفعل بهذا الرابط. يمكنك تسجيل الدخول مباشرة.",
    "INVITATION_EXPIRED": "انتهت صلاحية هذا الرابط. اطلب من المسؤول إرسال رابط جديد.",
    "INVITATION_REVOKED": "أُلغي هذا الرابط أو استُبدل برابط أحدث. استخدم آخر رابط وصلك أو اطلب رابطًا جديدًا.",
    "INVITATION_INVALID": "رابط الدعوة غير صحيح.",
}
STATE_TO_CODE = {
    "accepted": "INVITATION_ACCEPTED_ALREADY",
    "expired": "INVITATION_EXPIRED",
    "revoked": "INVITATION_REVOKED",
    "unknown": "INVITATION_INVALID",
}


def invitation_error(code: str) -> CodedError:
    return CodedError(code, INVITATION_MESSAGES[code])


class InvitationError(CodedError):
    """Kept for importers of the old name: an invitation link that cannot be used."""

    def __init__(self, code: str = "INVITATION_INVALID"):
        super().__init__(code, INVITATION_MESSAGES[code])


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:2]}***@{domain}" if domain else "***"


@dataclass
class Outgoing:
    """An e-mail to send once the database work is committed, plus the invitation to record the outcome on."""

    to: str
    subject: str
    body: str
    invitation: UserInvitation | None = None


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
    def _issue(
        cls, user: User, actor, template: PermissionTemplate | None, purpose: str = "invite"
    ) -> tuple[UserInvitation, str]:
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
            purpose=purpose,
        )
        return invitation, token

    @staticmethod
    def _deliver(outgoing: Outgoing) -> dict:
        """Send now (after the commit), record the outcome on the invitation and return it. Never raises."""

        error = ""
        try:
            send_mail(
                subject=outgoing.subject,
                message=outgoing.body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[outgoing.to],
                fail_silently=False,
            )
            sent = True
        except Exception as exc:  # noqa: BLE001 - the account exists; staff can resend the invitation
            logger.exception("invitation_email_failed")
            sent = False
            error = f"{type(exc).__name__}: {exc}"[:255]
        now = timezone.now()
        if outgoing.invitation is not None:
            UserInvitation.objects.filter(pk=outgoing.invitation.pk).update(
                last_send_status="ok" if sent else "failed",
                last_send_error="" if sent else error,
                last_sent_at=now,
            )
        return {"email_sent": sent, "status": "ok" if sent else "failed", "sent_at": now, "error": error or None}

    @classmethod
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
    ) -> tuple[User, dict]:
        """Create the staff account, then e-mail how to get in. Returns the user and the delivery outcome
        (``email_sent``, ``status``, ``sent_at``, ``error``). Secrets are never returned, they only travel by e-mail."""

        user, outgoing = cls._create_account(
            actor=actor,
            full_name=full_name,
            email=email,
            phone_number=phone_number,
            template=template,
            mode=mode,
            request=request,
        )
        return user, cls._deliver(outgoing)

    @classmethod
    @transaction.atomic
    def _create_account(
        cls, *, actor, full_name, email, phone_number, template, mode, request=None
    ) -> tuple[User, Outgoing]:
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
            outgoing = Outgoing(user.email, subject, body)
        else:
            invitation, token = cls._issue(user, actor, template)
            body = (
                f"مرحبًا {user.full_name}،\n\nدُعيت للانضمام إلى لوحة تحكم بانوراما.\n"
                f"افتح الرابط التالي لتحديد كلمة مرورك (صالح {settings.INVITATION_TTL_HOURS} ساعة ولمرة واحدة):\n"
                f"{cls.link(token)}"
            )
            outgoing = Outgoing(user.email, subject, body, invitation)
        AuditLogService.log(
            actor=actor,
            action=AuditAction.USER_INVITED,
            target=user,
            new_value={"template": template.key, "role": template.role, "mode": mode},
            request=request,
            critical=True,
        )
        return user, outgoing

    @staticmethod
    def is_set_up(user: User) -> bool:
        """The person already has a working password of their own (an unused temporary password does not count)."""

        return user.has_usable_password() and not user.must_change_password

    @classmethod
    def resend(cls, *, actor, user: User, request=None) -> dict:
        """Send a fresh link in every state. Not set up yet -> a new invitation link (password stays unusable until
        used). Already set up -> a password-reset link that leaves the current password working until it is used.
        Returns ``{purpose, email_sent, status, sent_at, error}``."""

        outgoing, purpose = cls._prepare_resend(actor=actor, user=user, request=request)
        return {"purpose": purpose, **cls._deliver(outgoing)}

    @classmethod
    @transaction.atomic
    def _prepare_resend(cls, *, actor, user: User, request=None) -> tuple[Outgoing, str]:
        if (
            user.role != UserRole.IT_SUPPORT
            and actor.role != UserRole.IT_SUPPORT
            and role_rank(user.role) >= role_rank(actor.role)
        ):
            raise PermissionDenied("You cannot manage an account with an equal or higher role.")
        if not user.is_active:
            raise CodedError("USER_INACTIVE", "الحساب معطّل. فعّل الحساب أولًا ثم أعد إرسال الدعوة.")
        purpose = "reset" if cls.is_set_up(user) else "invite"
        previous = user.invitations.order_by("-created_at").first()
        invitation, token = cls._issue(user, actor, previous.template if previous else None, purpose)
        if purpose == "invite":
            user.set_unusable_password()
            user.must_change_password = False
            user.save(update_fields=["password", "must_change_password", "updated_at"])
            subject = "رابط جديد للانضمام إلى بانوراما"
            body = (
                f"مرحبًا {user.full_name}،\n\nهذا رابط جديد لتحديد كلمة مرورك في لوحة تحكم بانوراما "
                f"(صالح {settings.INVITATION_TTL_HOURS} ساعة ولمرة واحدة):\n{cls.link(token)}"
            )
        else:
            subject = "رابط لإعادة تعيين كلمة المرور في بانوراما"
            body = (
                f"مرحبًا {user.full_name}،\n\nهذا رابط لإعادة تعيين كلمة المرور في لوحة تحكم بانوراما "
                f"(صالح {settings.INVITATION_TTL_HOURS} ساعة ولمرة واحدة). "
                f"كلمة مرورك الحالية تبقى صالحة حتى تحدد كلمة مرور جديدة:\n{cls.link(token)}"
            )
        AuditLogService.log(
            actor=actor,
            action=AuditAction.USER_INVITED,
            target=user,
            new_value={"resent": True, "purpose": purpose},
            request=request,
            critical=True,
        )
        return Outgoing(user.email, subject, body, invitation), purpose

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
    def find(token: str) -> UserInvitation | None:
        """The invitation behind a token in ANY state (None for garbage / never issued)."""

        if not token or len(token) > 128:
            return None
        return UserInvitation.objects.select_related("user").filter(token_hash=_hash(token), is_deleted=False).first()

    @staticmethod
    def state_of(invitation: UserInvitation | None) -> str:
        """valid | accepted | expired | revoked | unknown. A deactivated account's links count as revoked."""

        if invitation is None:
            return "unknown"
        state = invitation.state
        if state == "pending":
            return "valid" if invitation.user.is_active else "revoked"
        return state

    @classmethod
    def lookup(cls, token: str) -> UserInvitation | None:
        invitation = cls.find(token)
        return invitation if cls.state_of(invitation) == "valid" else None

    @classmethod
    def describe(cls, token: str) -> dict:
        """Public, non-sensitive view of a link: ``state`` always; PII only where it helps the person."""

        invitation = cls.find(token)
        state = cls.state_of(invitation)
        data: dict = {"state": state}
        if invitation is None:
            return data
        if state == "valid":
            data.update(
                full_name=invitation.user.full_name, expires_at=invitation.expires_at, purpose=invitation.purpose
            )
        elif state == "accepted":
            data.update(email_masked=mask_email(invitation.user.email), purpose=invitation.purpose)
        return data

    @classmethod
    @transaction.atomic
    def accept(cls, token: str, password: str, request=None) -> User:
        invitation = cls.find(token)
        state = cls.state_of(invitation)
        if invitation is None or state != "valid":
            raise invitation_error(STATE_TO_CODE[state])
        locked = UserInvitation.objects.select_for_update().select_related("user").get(pk=invitation.pk)
        state = cls.state_of(locked)
        if state != "valid":  # lost a race with a second submit of the same link
            raise invitation_error(STATE_TO_CODE[state])
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
        if locked.purpose == "reset":
            user.invalidate_sessions()  # whoever held the old password must not stay signed in
        AuditLogService.log(
            actor=user,
            action=AuditAction.USER_INVITATION_ACCEPTED,
            target=user,
            new_value={"purpose": locked.purpose},
            request=request,
            critical=True,
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
    def recent_logins(user: User, limit: int = 10) -> list[dict]:
        """The user's last login successes/failures (with the real failure reason) for the admin activity dialog."""

        from apps.audit.models import SystemActivity, SystemActivityKind

        rows = SystemActivity.objects.filter(
            user=user, kind__in=[SystemActivityKind.LOGIN_SUCCESS, SystemActivityKind.LOGIN_FAILED]
        ).order_by("-created_at", "-id")[:limit]
        return [
            {
                "id": row.pk,
                "kind": row.kind,
                "created_at": row.created_at,
                "ip": row.ip,
                "reason": (row.extra or {}).get("reason", "") if row.kind == SystemActivityKind.LOGIN_FAILED else "",
                "note": (row.extra or {}).get("note", ""),
            }
            for row in rows
        ]

    @staticmethod
    def invitation(user: User) -> dict:
        """``state``: pending | accepted | expired | revoked | none; ``mode`` is read from the invite audit row."""

        latest = user.invitations.order_by("-created_at", "-id").first()
        invited = (
            AuditLog.objects.filter(action=AuditAction.USER_INVITED, target_type="User", target_id=str(user.pk))
            .order_by("-created_at", "-id")
            .first()
        )
        mode = None
        if invited is not None:
            recorded = (invited.new_value or {}).get("mode")
            mode = recorded if recorded in {"temporary_password", "link"} else None
            if mode is None and (invited.new_value or {}).get("resent"):
                mode = "link"  # a resend always issues a fresh link
        if mode is None and latest is not None:
            mode = "link"
        state = "none" if latest is None else latest.state
        accepted = user.invitations.filter(accepted_at__isnull=False).order_by("-accepted_at").first()
        last_sent: datetime | None
        if latest is not None and latest.last_sent_at is not None:
            last_sent = latest.last_sent_at
        else:
            last_sent = invited.created_at if invited is not None else (latest.created_at if latest else None)
        delivery = None
        if latest is not None and latest.last_send_status:
            delivery = {
                "status": latest.last_send_status,
                "sent_at": latest.last_sent_at,
                "error": latest.last_send_error or None,
            }
        return {
            "state": state,
            "mode": mode,
            "purpose": latest.purpose if latest is not None else None,
            "last_sent_at": last_sent,
            "expires_at": latest.expires_at if latest is not None and state == "pending" else None,
            "accepted_at": accepted.accepted_at if accepted is not None else None,
            "password_set": user.has_usable_password(),
            "delivery": delivery,
        }

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
    "CodedError",
    "InvitationError",
    "InvitationService",
    "PermissionService",
    "StaffActivityService",
    "TemplateService",
    "can_manage_staff",
    "template_warnings",
]
