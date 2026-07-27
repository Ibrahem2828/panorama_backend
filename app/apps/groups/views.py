from __future__ import annotations

from django.db.models import Count, Prefetch, Q
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import filters, permissions, status
from rest_framework.views import APIView

from apps.accounts.permissions import CanManageExternalChannels, CanManageGroups, IsVerifiedStudent
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.responses import success_response
from apps.common.throttles import ExternalChannelRateThrottle
from apps.common.viewsets import StandardModelViewSet, StandardReadOnlyModelViewSet

from .models import (
    ExternalChannelAccessTicket,
    Group,
    GroupExternalChannel,
    GroupMembership,
    GroupMembershipStatus,
)
from .serializers import (
    DashboardGroupSerializer,
    ExternalChannelTicketSerializer,
    GroupMembershipRoleUpdateSerializer,
    GroupMembershipSerializer,
    GroupSerializer,
    WhatsAppChannelUpdateSerializer,
)
from .services import ExternalChannelService, GroupMembershipService, is_student_eligible_for_group


def _group_queryset():
    return (
        Group.objects.filter(is_deleted=False)
        .select_related("university", "faculty", "major", "academic_year", "semester", "subject", "created_by")
        .annotate(members_count=Count("memberships", filter=Q(memberships__status=GroupMembershipStatus.APPROVED)))
        .prefetch_related(
            Prefetch(
                "external_channels",
                queryset=GroupExternalChannel.objects.filter(is_active=True, is_deleted=False),
                to_attr="active_external_channels",
            )
        )
    )


class AvailableGroupViewSet(StandardReadOnlyModelViewSet):
    permission_classes = [IsVerifiedStudent]
    serializer_class = GroupSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["university", "faculty", "major", "academic_year", "semester", "subject"]
    search_fields = ["name", "description"]
    ordering_fields = ["name", "created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Group.objects.none()
        user = self.request.user
        profile = user.student_profile
        memberships = GroupMembership.objects.filter(user=user, is_deleted=False)
        queryset = (
            _group_queryset()
            .filter(university=profile.university, is_active=True)
            .prefetch_related(Prefetch("memberships", queryset=memberships, to_attr="current_memberships"))
        )
        eligible_ids = []
        for group in queryset:
            membership = group.current_memberships[0] if group.current_memberships else None
            group._current_membership = membership
            if membership and membership.status in {GroupMembershipStatus.APPROVED, GroupMembershipStatus.BLOCKED}:
                continue
            if is_student_eligible_for_group(user, group):
                eligible_ids.append(group.id)
        return queryset.filter(id__in=eligible_ids).order_by("name")


class MyGroupViewSet(StandardReadOnlyModelViewSet):
    permission_classes = [IsVerifiedStudent]
    serializer_class = GroupSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Group.objects.none()
        return (
            _group_queryset()
            .filter(
                memberships__user=self.request.user,
                memberships__status=GroupMembershipStatus.APPROVED,
                memberships__is_deleted=False,
                is_active=True,
            )
            .distinct()
            .order_by("name")
        )


class GroupDetailViewSet(StandardReadOnlyModelViewSet):
    permission_classes = [IsVerifiedStudent]
    serializer_class = GroupSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Group.objects.none()
        return _group_queryset().filter(is_active=True)

    def get_object(self):
        group = super().get_object()
        if not is_student_eligible_for_group(self.request.user, group):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("You are not eligible to view this group.")
        return group


class DashboardGroupViewSet(StandardModelViewSet):
    permission_classes = [CanManageGroups]
    serializer_class = DashboardGroupSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["university", "faculty", "major", "academic_year", "semester", "subject", "is_active"]
    search_fields = ["name", "description"]
    ordering_fields = ["name", "created_at"]

    def get_queryset(self):
        return _group_queryset()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class GroupJoinView(APIView):
    permission_classes = [IsVerifiedStudent]
    serializer_class = GroupMembershipSerializer

    @extend_schema(tags=["Groups"], responses={200: GroupMembershipSerializer})
    def post(self, request, pk: int):
        group = get_object_or_404(Group, pk=pk, is_active=True, is_deleted=False)
        membership = GroupMembershipService.join(request.user, group)
        return success_response(
            data=GroupMembershipSerializer(membership).data,
            message="Group join request submitted",
            request=request,
            code="GROUP_JOIN_REQUESTED",
        )


class GroupLeaveView(APIView):
    permission_classes = [IsVerifiedStudent]
    serializer_class = GroupMembershipSerializer

    @extend_schema(tags=["Groups"])
    def post(self, request, pk: int):
        group = get_object_or_404(Group, pk=pk, is_deleted=False)
        GroupMembershipService.leave(request.user, group)
        return success_response(message="Left group successfully", request=request, code="GROUP_LEFT")


class DashboardGroupMembershipViewSet(StandardReadOnlyModelViewSet):
    only_pending = False
    permission_classes = [CanManageGroups]
    serializer_class = GroupMembershipSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["status", "role"]
    search_fields = ["user__full_name", "user__email", "group__name"]
    ordering_fields = ["created_at", "reviewed_at", "joined_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return GroupMembership.objects.none()
        queryset = GroupMembership.objects.filter(
            group_id=self.kwargs["group_pk"],
            is_deleted=False,
        ).select_related("group", "user")
        if getattr(self, "only_pending", False):
            queryset = queryset.filter(status=GroupMembershipStatus.PENDING)
        return queryset


class MembershipReviewView(APIView):
    permission_classes = [CanManageGroups]
    serializer_class = GroupMembershipSerializer
    target_status = GroupMembershipStatus.APPROVED

    @extend_schema(tags=["Dashboard"], responses={200: GroupMembershipSerializer})
    def post(self, request, pk: int):
        membership = get_object_or_404(
            GroupMembership.objects.select_related("group", "user"),
            pk=pk,
            is_deleted=False,
        )
        membership = GroupMembershipService.review(membership, request.user, self.target_status, request=request)
        return success_response(
            data=GroupMembershipSerializer(membership).data,
            message="Membership updated successfully",
            status_code=status.HTTP_200_OK,
            request=request,
            code="GROUP_MEMBERSHIP_UPDATED",
        )


class ApproveMembershipView(MembershipReviewView):
    target_status = GroupMembershipStatus.APPROVED


class RejectMembershipView(MembershipReviewView):
    target_status = GroupMembershipStatus.REJECTED


class BlockMembershipView(MembershipReviewView):
    target_status = GroupMembershipStatus.BLOCKED


class MembershipRoleUpdateView(APIView):
    permission_classes = [CanManageGroups]
    serializer_class = GroupMembershipRoleUpdateSerializer

    @extend_schema(tags=["Dashboard"], request=GroupMembershipRoleUpdateSerializer, responses={200: GroupMembershipSerializer})
    def patch(self, request, pk: int):
        membership = get_object_or_404(
            GroupMembership.objects.select_related("group", "user"),
            pk=pk,
            is_deleted=False,
        )
        serializer = GroupMembershipRoleUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership.role = serializer.validated_data["role"]
        membership.save(update_fields=["role", "updated_at"])
        return success_response(
            data=GroupMembershipSerializer(membership).data,
            message="Membership role updated successfully",
            request=request,
        )


class DashboardWhatsAppChannelView(APIView):
    permission_classes = [CanManageExternalChannels]
    serializer_class = WhatsAppChannelUpdateSerializer

    @extend_schema(tags=["Dashboard"], request=WhatsAppChannelUpdateSerializer)
    def put(self, request, pk: int):
        group = get_object_or_404(Group, pk=pk, is_deleted=False)
        serializer = WhatsAppChannelUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        channel = ExternalChannelService.set_whatsapp_channel(
            group,
            serializer.validated_data["url"],
            actor=request.user,
            is_active=serializer.validated_data["is_active"],
            label=serializer.validated_data.get("label", ""),
        )
        AuditLogService.log(
            actor=request.user,
            action=AuditAction.GROUP_EXTERNAL_CHANNEL_UPDATED,
            target=group,
            new_value={"channel_type": channel.channel_type, "is_active": channel.is_active},
            request=request,
        )
        return success_response(
            data={"has_whatsapp_channel": channel.is_active},
            message="WhatsApp channel updated without exposing the raw link",
            request=request,
            code="EXTERNAL_CHANNEL_UPDATED",
        )


class GroupWhatsAppTicketView(APIView):
    permission_classes = [IsVerifiedStudent]
    throttle_classes = [ExternalChannelRateThrottle]
    serializer_class = ExternalChannelTicketSerializer

    @extend_schema(tags=["Groups"], responses={201: ExternalChannelTicketSerializer})
    def post(self, request, pk: int):
        group = get_object_or_404(Group, pk=pk, is_active=True, is_deleted=False)
        ticket = ExternalChannelService.issue_ticket(group, request.user)
        open_url = request.build_absolute_uri(f"/api/v1/external-channels/open/{ticket.token}/")
        AuditLogService.log(
            actor=request.user,
            action=AuditAction.GROUP_EXTERNAL_CHANNEL_TICKET_ISSUED,
            target=group,
            new_value={"ticket_id": ticket.id, "expires_at": ticket.expires_at.isoformat()},
            request=request,
        )
        return success_response(
            data={"open_url": open_url, "expires_at": ticket.expires_at},
            message="Temporary external channel link issued",
            status_code=status.HTTP_201_CREATED,
            request=request,
            code="EXTERNAL_CHANNEL_TICKET_ISSUED",
        )


class ExternalChannelRedirectView(APIView):
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    @extend_schema(auth=[], tags=["Protected Assets"], responses={302: OpenApiResponse(description="One-time redirect")})
    def get(self, request, token):
        ticket = (
            ExternalChannelAccessTicket.objects.select_related("channel__group", "user")
            .filter(token=token, is_deleted=False)
            .first()
        )
        if not ticket or not ticket.is_valid:
            raise Http404("The external channel link is invalid or expired.")
        destination = ExternalChannelService.consume_ticket(ticket)
        AuditLogService.log(
            actor=ticket.user,
            action=AuditAction.GROUP_EXTERNAL_CHANNEL_OPENED,
            target=ticket.channel.group,
            new_value={"channel_type": ticket.channel.channel_type},
            request=request,
        )
        response = HttpResponseRedirect(destination)
        response["Cache-Control"] = "no-store, max-age=0"
        response["Referrer-Policy"] = "no-referrer"
        return response
