from rest_framework.exceptions import PermissionDenied, ValidationError
from django.conf import settings
from django.core import signing
from django.db import transaction

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.common.upload_validation import validate_document_upload, validate_image_upload
from apps.groups.models import Group, GroupMembershipRole, GroupMembershipStatus

from .models import Message, MessageType


GROUP_CHAT_WS_TOKEN_SALT = "panorama.group_chat_ws.v1"


class ChatPermissionService:
    @staticmethod
    def can_access_group_chat(user, group) -> bool:
        if not user or not user.is_authenticated:
            return False
        if getattr(group, "is_deleted", False) or not getattr(group, "is_active", True):
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
    @transaction.atomic
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


class ChatWebSocketTokenService:
    purpose = "group_chat_ws"

    @staticmethod
    def create_token(user, group: Group, request=None) -> dict:
        ChatPermissionService.enforce_group_chat_access(user, group)
        expires_in = int(settings.GROUP_CHAT_WS_TOKEN_TTL_SECONDS)
        payload = {
            "user_id": user.id,
            "group_id": group.id,
            "purpose": ChatWebSocketTokenService.purpose,
            "expires_in": expires_in,
        }
        ws_token = signing.dumps(payload, salt=GROUP_CHAT_WS_TOKEN_SALT)
        host = request.get_host() if request is not None else "localhost:8000"
        scheme = "wss" if request is not None and request.is_secure() else "ws"
        websocket_url = f"{scheme}://{host}/ws/v1/groups/{group.id}/chat/?token={ws_token}"
        return {"ws_token": ws_token, "expires_in": expires_in, "websocket_url": websocket_url}

    @staticmethod
    def validate_token(token: str) -> dict:
        if not token:
            raise PermissionDenied("Missing WebSocket token.")
        try:
            payload = signing.loads(
                token,
                salt=GROUP_CHAT_WS_TOKEN_SALT,
                max_age=settings.GROUP_CHAT_WS_TOKEN_TTL_SECONDS,
            )
        except signing.SignatureExpired as exc:
            raise PermissionDenied("WebSocket token has expired.") from exc
        except signing.BadSignature as exc:
            raise PermissionDenied("Invalid WebSocket token.") from exc
        if payload.get("purpose") != ChatWebSocketTokenService.purpose:
            raise PermissionDenied("Invalid WebSocket token.")
        try:
            return {
                "user_id": int(payload["user_id"]),
                "group_id": int(payload["group_id"]),
                "purpose": payload["purpose"],
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise PermissionDenied("Invalid WebSocket token.") from exc
