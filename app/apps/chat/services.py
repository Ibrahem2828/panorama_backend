from __future__ import annotations

from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.choices import StudentVerificationStatus
from apps.accounts.permissions import Capability, PermissionService
from apps.groups.models import GroupMembershipRole, GroupMembershipStatus

from .models import Message, MessageType


class ChatPermissionService:
    @staticmethod
    def can_access_group_chat(user, group) -> bool:
        if not user or not user.is_authenticated or not user.is_active or group.is_deleted:
            return False
        if PermissionService.has(user, Capability.GROUPS_MANAGE):
            return True
        profile = getattr(user, "student_profile", None)
        if not profile or profile.verification_status != StudentVerificationStatus.APPROVED:
            return False
        return user.group_memberships.filter(group=group, status=GroupMembershipStatus.APPROVED).exists()

    @staticmethod
    def get_membership(user, group):
        if not user or not user.is_authenticated:
            return None
        return user.group_memberships.filter(group=group, status=GroupMembershipStatus.APPROVED).first()

    @staticmethod
    def can_send_message(user, group) -> bool:
        if not ChatPermissionService.can_access_group_chat(user, group):
            return False
        if PermissionService.has(user, Capability.GROUPS_MANAGE):
            return True
        membership = ChatPermissionService.get_membership(user, group)
        if not membership:
            return False
        if group.send_messages_permission == "all_members":
            return True
        return membership.role in {GroupMembershipRole.MODERATOR, GroupMembershipRole.GROUP_ADMIN}

    @staticmethod
    def can_moderate_messages(user, group) -> bool:
        if PermissionService.has(user, Capability.GROUPS_MANAGE):
            return True
        membership = ChatPermissionService.get_membership(user, group)
        return bool(membership and membership.role in {GroupMembershipRole.MODERATOR, GroupMembershipRole.GROUP_ADMIN})

    @staticmethod
    def enforce_group_chat_access(user, group):
        if not ChatPermissionService.can_access_group_chat(user, group):
            raise PermissionDenied("You are not allowed to access this group chat.")

    @staticmethod
    def enforce_can_send_message(user, group):
        if not ChatPermissionService.can_send_message(user, group):
            raise PermissionDenied("You are not allowed to send messages in this group.")


class ChatMessageService:
    @staticmethod
    def create_message(
        group,
        sender,
        content: str = "",
        message_type: str = MessageType.TEXT,
        attachment=None,
        reply_to=None,
    ) -> Message:
        ChatPermissionService.enforce_group_chat_access(sender, group)
        ChatPermissionService.enforce_can_send_message(sender, group)
        content = str(content or "").strip()
        if len(content) > 4000:
            raise ValidationError({"content": "Messages cannot exceed 4000 characters."})
        if message_type == MessageType.TEXT and not content:
            raise ValidationError({"content": "Content is required for text messages."})
        if message_type in {MessageType.IMAGE, MessageType.FILE} and not attachment:
            raise ValidationError({"attachment": "Attachment is required for file or image messages."})
        if message_type == MessageType.SYSTEM:
            raise ValidationError({"message_type": "System messages are server generated."})
        if reply_to and (reply_to.group_id != group.id or reply_to.is_deleted):
            raise ValidationError({"reply_to": "The referenced message is not available in this group."})
        return Message.objects.create(
            group=group,
            sender=sender,
            content=content,
            message_type=message_type,
            attachment=attachment,
            reply_to=reply_to,
        )
