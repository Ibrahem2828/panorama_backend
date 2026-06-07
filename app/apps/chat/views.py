from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import filters, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.views import APIView

from apps.accounts.choices import UserRole
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.responses import success_response
from apps.common.viewsets import StandardReadOnlyModelViewSet
from apps.groups.models import Group

from .models import Message, MessageReport
from .serializers import MessageCreateSerializer, MessageReportSerializer, MessageSerializer
from .services import ChatPermissionService


class GroupMessageViewSet(StandardReadOnlyModelViewSet):
    serializer_class = MessageSerializer
    throttle_scope = "chat_message"
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ["created_at"]
    ordering = ["created_at"]

    def get_throttles(self):
        if getattr(self, "action", None) != "create":
            return []
        return super().get_throttles()

    def get_group(self):
        group = Group.objects.get(pk=self.kwargs["group_id"], is_deleted=False)
        ChatPermissionService.enforce_group_chat_access(self.request.user, group)
        return group

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Message.objects.none()
        return Message.objects.filter(group=self.get_group(), is_deleted=False).select_related("sender")

    @extend_schema(tags=["Chat"], request=MessageCreateSerializer, responses={201: MessageSerializer})
    def create(self, request, *args, **kwargs):
        group = self.get_group()
        serializer = MessageCreateSerializer(data=request.data, context={"request": request, "group": group})
        serializer.is_valid(raise_exception=True)
        message = serializer.save()
        return success_response(data=MessageSerializer(message).data, message="Message sent successfully", status_code=status.HTTP_201_CREATED)


class GroupMessageDeleteView(APIView):
    serializer_class = MessageSerializer

    @extend_schema(tags=["Chat"])
    def delete(self, request, group_id: int, message_id: int):
        with transaction.atomic():
            group = Group.objects.get(pk=group_id, is_deleted=False)
            message = Message.objects.select_for_update().get(pk=message_id, group=group)
            if not ChatPermissionService.can_moderate_messages(request.user, group) and message.sender_id != request.user.id:
                from rest_framework.exceptions import PermissionDenied

                raise PermissionDenied("You cannot delete this message.")
            ChatPermissionService.enforce_group_chat_access(request.user, group)
            if not message.is_deleted:
                message.soft_delete(request.user)
                AuditLogService.log(actor=request.user, action=AuditAction.MESSAGE_DELETED, target=message)
        return success_response(message="Message deleted successfully")


class GroupMessageReportView(APIView):
    serializer_class = MessageReportSerializer

    @extend_schema(tags=["Chat"], request=MessageReportSerializer, responses={201: MessageReportSerializer})
    def post(self, request, group_id: int, message_id: int):
        with transaction.atomic():
            group = Group.objects.get(pk=group_id, is_deleted=False)
            ChatPermissionService.enforce_group_chat_access(request.user, group)
            message = Message.objects.select_for_update().get(pk=message_id, group=group, is_deleted=False)
            serializer = MessageReportSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            report = MessageReport.objects.create(message=message, reported_by=request.user, reason=serializer.validated_data["reason"])
        return success_response(data=MessageReportSerializer(report).data, message="Message reported successfully", status_code=status.HTTP_201_CREATED)
