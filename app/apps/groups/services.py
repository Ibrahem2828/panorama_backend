from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status as http_status
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.permissions import Capability, PermissionService
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.crypto import decrypt_text, encrypt_text
from apps.notifications.models import NotificationType
from apps.notifications.services import NotificationService

from .models import (
    ExternalChannelType,
    Group,
    GroupExternalChannel,
    GroupMembership,
    GroupMembershipStatus,
)

WHATSAPP_HOSTS = {"wa.me", "www.wa.me", "api.whatsapp.com", "chat.whatsapp.com", "www.whatsapp.com"}


ENROLLMENT_ONLY_MESSAGE = "الانضمام إلى المجموعات يتم تلقائيًا بعد قبول طلب تسجيل المواد."


class GroupJoinEnrollmentOnly(APIException):
    """A student asked to join a group by hand. Groups are filled only from approved subject-registration requests
    (and the faculty community on verification), never by a join request (HTTP 403, stable code)."""

    status_code = http_status.HTTP_403_FORBIDDEN
    default_detail = ENROLLMENT_ONLY_MESSAGE
    default_code = "group_join_enrollment_only"
    api_code = "GROUP_JOIN_ENROLLMENT_ONLY"


def is_student_eligible_for_group(user, group: Group) -> bool:
    if user.role != UserRole.STUDENT:
        return False
    profile = getattr(user, "student_profile", None)
    if not profile or profile.verification_status != StudentVerificationStatus.APPROVED:
        return False
    if group.university_id and profile.university_id != group.university_id:
        return False
    if group.faculty_id and profile.faculty_id != group.faculty_id:
        return False
    if group.major_id and profile.major_id != group.major_id:
        return False
    if group.academic_year_id and profile.academic_year_id != group.academic_year_id:
        return False
    if group.semester_id and profile.semester_id != group.semester_id:
        return False
    return True


def eligible_groups_filter(user) -> Q:
    """Database twin of ``is_student_eligible_for_group``: groups this student may see/join by hierarchy.

    A group that pins a faculty/major/year/semester only matches students with that exact value; an unpinned level
    matches everyone in the university. Non-students and unverified students match nothing.
    """

    profile = getattr(user, "student_profile", None)
    if (
        user.role != UserRole.STUDENT
        or not profile
        or profile.verification_status != StudentVerificationStatus.APPROVED
        or not profile.university_id
    ):
        return Q(pk__in=[])
    condition = Q(university_id=profile.university_id)
    for field in ("faculty", "major", "academic_year", "semester"):
        value = getattr(profile, f"{field}_id")
        condition &= Q(**{f"{field}__isnull": True}) | (Q(**{f"{field}_id": value}) if value else Q(pk__in=[]))
    return condition


def visible_groups_for_student(user):
    """Active groups a verified student may see: eligible by hierarchy plus those they already belong to.

    Same rule as the ``/groups/`` collection (``GroupDetailViewSet`` list); non-students and unverified students get
    nothing because ``eligible_groups_filter`` matches nothing for them and they hold no approved membership.
    """

    member_group_ids = GroupMembership.objects.filter(
        user=user, status=GroupMembershipStatus.APPROVED, is_deleted=False
    ).values("group_id")
    return Group.objects.filter(is_deleted=False, is_active=True).filter(
        eligible_groups_filter(user) | Q(pk__in=member_group_ids)
    )


class GroupMembershipService:
    @staticmethod
    @transaction.atomic
    def join(user, group: Group, *, initiated_by=None) -> GroupMembership:
        """Open a membership (pending or approved per ``requires_approval``) on behalf of someone else.

        A student can never join a group by asking: when ``initiated_by`` is missing or is ``user`` themselves this
        refuses. Legitimate additions use ``enrollment.services.add_to_group`` (approved enrollment, faculty community)
        or this method with a staff ``initiated_by``.
        """

        if initiated_by is None or initiated_by.pk == user.pk:
            raise GroupJoinEnrollmentOnly()
        group = Group.objects.select_for_update().get(pk=group.pk)
        if not group.is_active or group.is_deleted:
            raise ValidationError("Group is not active.")
        if group.subject_id:
            # Subject groups are filled from approved enrollment requests (Learnata screenshot), never by hand.
            raise ValidationError(
                {"detail": "Register your subjects to join this group.", "code": "ENROLLMENT_REQUIRED"}
            )
        if not is_student_eligible_for_group(user, group):
            raise ValidationError("You are not eligible to join this group.")

        membership = GroupMembership.objects.select_for_update().filter(group=group, user=user).first()
        if membership and membership.status == GroupMembershipStatus.BLOCKED:
            raise ValidationError("You are blocked from this group.")
        if membership and membership.status in {GroupMembershipStatus.PENDING, GroupMembershipStatus.APPROVED}:
            raise ValidationError("You already have an active membership for this group.")

        status = GroupMembershipStatus.PENDING if group.requires_approval else GroupMembershipStatus.APPROVED
        joined_at = timezone.now() if status == GroupMembershipStatus.APPROVED else None
        if membership:
            membership.status = status
            membership.joined_at = joined_at
            membership.reviewed_by = None
            membership.reviewed_at = None
            membership.ended_at = None
            membership.rollover = None
            membership.save()
        else:
            membership = GroupMembership.objects.create(group=group, user=user, status=status, joined_at=joined_at)
        if status == GroupMembershipStatus.PENDING:
            transaction.on_commit(lambda: GroupMembershipService.notify_join_request(membership.pk))
        return membership

    @staticmethod
    def notify_join_request(membership_id: int) -> None:
        """Tell group managers a student asks to join (best effort; ``notify_staff`` swallows its own failures)."""

        membership = GroupMembership.objects.select_related("group", "user").filter(pk=membership_id).first()
        if membership is None or membership.status != GroupMembershipStatus.PENDING:
            return
        from apps.notifications.staff import notify_staff

        notify_staff(
            "join_request",
            "طلب انضمام جديد إلى مجموعة",
            f"{membership.user.full_name} يطلب الانضمام إلى {membership.group.name}.",
            # One event per request (a student can ask again after a rejection); the page opens the group.
            object_id=f"{membership.pk}:{int(membership.updated_at.timestamp())}",
            highlight=membership.group_id,
            actor_id=membership.user_id,
            extra={"group_id": membership.group_id, "membership_id": membership.pk},
        )

    @staticmethod
    @transaction.atomic
    def leave(user, group: Group) -> GroupMembership:
        membership = GroupMembership.objects.select_for_update().get(
            group=group,
            user=user,
            status=GroupMembershipStatus.APPROVED,
        )
        membership.status = GroupMembershipStatus.LEFT
        membership.save(update_fields=["status", "updated_at"])
        return membership

    @staticmethod
    @transaction.atomic
    def review(
        membership: GroupMembership,
        reviewer,
        status: str,
        request=None,
        *,
        note: str = "",
        only_if_pending: bool = False,
    ) -> GroupMembership:
        membership = GroupMembership.objects.select_for_update().select_related("group", "user").get(pk=membership.pk)
        if only_if_pending and membership.status != GroupMembershipStatus.PENDING:
            raise ValidationError({"detail": "This request is no longer pending.", "code": "NOT_PENDING"})
        note = (note or "").strip()
        if status == GroupMembershipStatus.APPROVED:
            membership.approve(reviewer)
            title = "تم قبول طلب الانضمام"
            body = f"تم قبول طلبك للانضمام إلى {membership.group.name}."
            action = AuditAction.GROUP_MEMBERSHIP_APPROVED
        else:
            membership.status = status
            membership.reviewed_by = reviewer
            membership.reviewed_at = timezone.now()
            membership.save()
            title = "تم تحديث عضوية الغروب"
            body = f"تم تحديث حالة عضويتك في {membership.group.name}."
            if note:
                body = f"{body} {note}"
            action = (
                AuditAction.GROUP_MEMBERSHIP_BLOCKED
                if status == GroupMembershipStatus.BLOCKED
                else AuditAction.GROUP_MEMBERSHIP_REJECTED
            )
        NotificationService.create_notification(
            membership.user,
            title=title,
            body=body,
            type=NotificationType.GROUP,
            data={"group_id": membership.group_id, "membership_id": membership.id, "status": status},
        )
        AuditLogService.log(
            actor=reviewer,
            action=action,
            target=membership,
            new_value={"status": status, "group_id": membership.group_id, **({"note": note} if note else {})},
            request=request,
        )
        return membership


class ExternalChannelService:
    @staticmethod
    def whatsapp_app_link(url: str) -> str | None:
        """The ``whatsapp://`` deep link for a stored https WhatsApp link, or None when it cannot be derived safely.

        Group invites (``chat.whatsapp.com/<code>``) open the invite inside the app; chat links (``wa.me/<digits>`` and
        ``api.whatsapp.com/send?phone=<digits>``) open that conversation. The https link itself is never sent to a client.
        """
        parsed = urlparse(str(url or "").strip())
        host = parsed.hostname or ""
        if host == "chat.whatsapp.com":
            code = parsed.path.strip("/").split("/")[0]
            return f"whatsapp://chat?code={code}" if re.fullmatch(r"[A-Za-z0-9]{10,40}", code) else None
        digits = ""
        if host in {"wa.me", "www.wa.me"}:
            digits = parsed.path.strip("/").split("/")[0]
        elif host in {"api.whatsapp.com", "www.whatsapp.com"}:
            digits = (parse_qs(parsed.query).get("phone") or [""])[0]
        digits = digits.lstrip("+")
        return f"whatsapp://send?phone={digits}" if re.fullmatch(r"\d{6,15}", digits) else None

    @classmethod
    def issue_app_link(cls, group: Group, user) -> str:
        """Authorise ``user`` for the group's WhatsApp channel and return the app deep link (not the https link)."""
        channel = GroupExternalChannel.objects.filter(
            group=group, channel_type=ExternalChannelType.WHATSAPP, is_active=True, is_deleted=False
        ).first()
        if not channel:
            raise ValidationError("WhatsApp channel is not available for this group.")
        if not cls.user_can_open(user, channel):
            raise PermissionDenied("You are not allowed to access this external channel.")
        link = cls.whatsapp_app_link(decrypt_text(channel.encrypted_url))
        if not link:
            raise ValidationError("WhatsApp channel is not available for this group.")
        return link

    @staticmethod
    def validate_whatsapp_url(value: str) -> str:
        value = str(value or "").strip()
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.hostname not in WHATSAPP_HOSTS:
            raise ValidationError({"url": "Only official HTTPS WhatsApp links are allowed."})
        if len(value) > 2048:
            raise ValidationError({"url": "The link is too long."})
        return value

    @classmethod
    @transaction.atomic
    def set_whatsapp_channel(cls, group: Group, url: str, actor, is_active: bool = True, label: str = ""):
        url = cls.validate_whatsapp_url(url)
        channel, _ = GroupExternalChannel.objects.select_for_update().get_or_create(
            group=group,
            channel_type=ExternalChannelType.WHATSAPP,
            defaults={"encrypted_url": encrypt_text(url)},
        )
        channel.encrypted_url = encrypt_text(url)
        channel.is_active = is_active
        channel.label = label.strip()[:100]
        channel.updated_by = actor
        channel.is_deleted = False
        channel.save()
        return channel

    @staticmethod
    def user_can_open(user, channel: GroupExternalChannel) -> bool:
        if PermissionService.has(user, Capability.GROUPS_EXTERNAL_CHANNELS_MANAGE):
            return True
        return user.group_memberships.filter(
            group=channel.group,
            status=GroupMembershipStatus.APPROVED,
            is_deleted=False,
        ).exists()
