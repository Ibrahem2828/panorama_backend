from rest_framework.exceptions import PermissionDenied, ValidationError
from django.conf import settings

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.common.upload_validation import validate_document_upload, validate_image_upload
from apps.groups.models import GroupMembershipRole, GroupMembershipStatus

from .models import Message, MessageType


class ChatPermissionService:
    @staticmethod
    def can_access_group_chat(user, group) -> bool:
        if not user or not user.is_authenticated:
            return False
        if user.role in {UserRole.ADMIN, UserRole.IT_SUPPORT}:
            return True
        profile = getattr(user, "student_profile", None)
        if user.role != UserRole.STUDENT or not profile or profile.verification_status != StudentVerificationStatus.APPROVED:
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
        if user.role in {UserRole.ADMIN, UserRole.IT_SUPPORT}:
            return True
        membership = ChatPermissionService.get_membership(user, group)
        if not membership:
            return False
        if group.send_messages_permission == "all_members":
            return True
        return membership.role in {GroupMembershipRole.MODERATOR, GroupMembershipRole.GROUP_ADMIN}

    @staticmethod
    def can_moderate_messages(user, group) -> bool:
        if user.role in {UserRole.ADMIN, UserRole.IT_SUPPORT}:
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
    def create_message(group, sender, content: str = "", message_type: str = MessageType.TEXT, attachment=None, reply_to=None) -> Message:
        ChatPermissionService.enforce_group_chat_access(sender, group)
        ChatPermissionService.enforce_can_send_message(sender, group)
        normalized_content = content.strip()
        if message_type == MessageType.TEXT and not content.strip():
            raise ValidationError({"content": "Content is required for text messages."})
        if normalized_content and len(normalized_content) > settings.MAX_CHAT_MESSAGE_LENGTH:
            raise ValidationError({"content": f"Content must be {settings.MAX_CHAT_MESSAGE_LENGTH} characters or fewer."})
        if message_type in {MessageType.IMAGE, MessageType.FILE} and not attachment:
            raise ValidationError({"attachment": "Attachment is required for file or image messages."})
        if attachment and message_type == MessageType.IMAGE:
            validate_image_upload(attachment)
        if attachment and message_type == MessageType.FILE:
            validate_document_upload(attachment)
        if reply_to and reply_to.group_id != group.id:
            raise ValidationError({"reply_to": "Reply target must belong to the same group."})
        return Message.objects.create(
            group=group,
            sender=sender,
            content=normalized_content if message_type == MessageType.TEXT else content,
            message_type=message_type,
            attachment=attachment,
            reply_to=reply_to,
        )
