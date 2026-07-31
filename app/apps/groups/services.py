from __future__ import annotations

from urllib.parse import urlparse

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.permissions import Capability, PermissionService
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.crypto import decrypt_text, encrypt_text
from apps.notifications.models import NotificationType
from apps.notifications.services import NotificationService

from .models import (
    ExternalChannelAccessTicket,
    ExternalChannelType,
    Group,
    GroupExternalChannel,
    GroupMembership,
    GroupMembershipStatus,
)

WHATSAPP_HOSTS = {"wa.me", "www.wa.me", "api.whatsapp.com", "chat.whatsapp.com", "www.whatsapp.com"}


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


class GroupMembershipService:
    @staticmethod
    @transaction.atomic
    def join(user, group: Group) -> GroupMembership:
        group = Group.objects.select_for_update().get(pk=group.pk)
        if not group.is_active or group.is_deleted:
            raise ValidationError("Group is not active.")
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
            membership.save()
            return membership
        return GroupMembership.objects.create(group=group, user=user, status=status, joined_at=joined_at)

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
    def review(membership: GroupMembership, reviewer, status: str, request=None) -> GroupMembership:
        membership = GroupMembership.objects.select_for_update().select_related("group", "user").get(pk=membership.pk)
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
            new_value={"status": status, "group_id": membership.group_id},
            request=request,
        )
        return membership


class ExternalChannelService:
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

    @classmethod
    def issue_ticket(cls, group: Group, user) -> ExternalChannelAccessTicket:
        channel = GroupExternalChannel.objects.filter(
            group=group,
            channel_type=ExternalChannelType.WHATSAPP,
            is_active=True,
            is_deleted=False,
        ).first()
        if not channel:
            raise ValidationError("WhatsApp channel is not available for this group.")
        if not cls.user_can_open(user, channel):
            raise PermissionDenied("You are not allowed to access this external channel.")
        return ExternalChannelAccessTicket.issue(channel, user)

    @staticmethod
    @transaction.atomic
    def consume_ticket(ticket: ExternalChannelAccessTicket) -> str:
        ticket = (
            ExternalChannelAccessTicket.objects.select_for_update().select_related("channel__group").get(pk=ticket.pk)
        )
        if not ticket.is_valid:
            raise ValidationError("The external channel link is invalid or expired.")
        ticket.used_at = timezone.now()
        ticket.save(update_fields=["used_at", "updated_at"])
        return decrypt_text(ticket.channel.encrypted_url)
