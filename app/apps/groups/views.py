from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import cast

import django_filters
from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework import filters, permissions, status
from rest_framework.exceptions import APIException
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.accounts.permissions import CanManageExternalChannels, CanManageGroups, IsStudent, IsVerifiedStudent
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.filters import DateBoundFilter, PositiveIntegerFilter, StrictBooleanFilter, count_subquery
from apps.common.idempotency import run_idempotent
from apps.common.negotiation import IgnoreClientContentNegotiation
from apps.common.responses import success_response
from apps.common.throttles import ExternalChannelRateThrottle
from apps.common.viewsets import StandardModelViewSet, StandardReadOnlyModelViewSet

from .models import (
    ExternalChannelType,
    Group,
    GroupExternalChannel,
    GroupMembership,
    GroupMembershipStatus,
)
from .serializers import (
    AvailableGroupSerializer,
    DashboardGroupSerializer,
    ExternalChannelAppLinkSerializer,
    ExternalChannelTicketSerializer,
    GroupMembershipRoleUpdateSerializer,
    GroupMembershipSerializer,
    GroupSerializer,
    WhatsAppChannelUpdateSerializer,
)
from .services import (
    ExternalChannelService,
    GroupJoinEnrollmentOnly,
    GroupMembershipService,
    eligible_groups_filter,
    is_student_eligible_for_group,
)


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
    """Read-only list of the groups that match the student's level. Joining by request no longer exists: every row
    carries ``can_request_join: false`` and groups are joined automatically after a subject-registration request is
    approved."""

    permission_classes = [IsVerifiedStudent]
    serializer_class = AvailableGroupSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["university", "faculty", "major", "academic_year", "semester", "subject"]
    search_fields = ["name", "description"]
    ordering_fields = ["name", "created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Group.objects.none()
        user = cast(User, self.request.user)
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
        user = cast(User, self.request.user)
        memberships = GroupMembership.objects.filter(user=user, is_deleted=False)
        queryset = (
            _group_queryset()
            .filter(is_active=True)
            .prefetch_related(Prefetch("memberships", queryset=memberships, to_attr="current_memberships"))
        )
        if self.action == "list":
            # The collection only contains groups this student may see: their university's eligible groups plus
            # groups they already belong to. retrieve keeps its explicit eligibility check in get_object().
            member_group_ids = GroupMembership.objects.filter(
                user=user, status=GroupMembershipStatus.APPROVED, is_deleted=False
            ).values("group_id")
            member_of = Q(pk__in=member_group_ids)
            queryset = queryset.filter(eligible_groups_filter(user) | member_of)
        return queryset.order_by("name", "id")

    def get_object(self):
        group = super().get_object()
        if not is_student_eligible_for_group(self.request.user, group):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("You are not eligible to view this group.")
        return group


GROUP_KINDS = ("subject", "faculty", "general")


def _approved_memberships():
    return GroupMembership.objects.filter(status=GroupMembershipStatus.APPROVED, is_deleted=False)


def _pending_memberships():
    return GroupMembership.objects.filter(status=GroupMembershipStatus.PENDING, is_deleted=False)


def _whatsapp_channels():
    return GroupExternalChannel.objects.filter(
        channel_type=ExternalChannelType.WHATSAPP, is_active=True, is_deleted=False
    )


def group_kind_q(kind: str, prefix: str = "") -> Q:
    """subject = pinned to a subject; faculty = pinned to a faculty but no subject; general = neither.

    ``prefix`` (e.g. ``"group__"``) applies the same rule through a relation.
    """

    if kind == "subject":
        return Q(**{f"{prefix}subject__isnull": False})
    if kind == "faculty":
        return Q(**{f"{prefix}subject__isnull": True, f"{prefix}faculty__isnull": False})
    return Q(**{f"{prefix}subject__isnull": True, f"{prefix}faculty__isnull": True})


class DashboardGroupFilter(django_filters.FilterSet):
    kind = django_filters.ChoiceFilter(choices=[(k, k) for k in GROUP_KINDS], method="filter_kind")
    requires_approval = StrictBooleanFilter(field_name="requires_approval")
    is_active = StrictBooleanFilter(field_name="is_active")
    has_whatsapp = StrictBooleanFilter(method="filter_has_whatsapp")
    send_messages_permission = django_filters.ChoiceFilter(
        choices=Group._meta.get_field("send_messages_permission").choices
    )
    has_pending_requests = StrictBooleanFilter(method="filter_has_pending_requests")
    members_min = PositiveIntegerFilter(field_name="members_count", lookup_expr="gte")
    members_max = PositiveIntegerFilter(field_name="members_count", lookup_expr="lte")
    created_after = DateBoundFilter(field_name="created_at", bound="after")
    created_before = DateBoundFilter(field_name="created_at", bound="before")

    class Meta:
        model = Group
        fields = ["university", "faculty", "major", "academic_year", "semester", "subject"]

    def filter_kind(self, queryset, name, value):
        return queryset.filter(group_kind_q(value)) if value else queryset

    def filter_has_whatsapp(self, queryset, name, value):
        if value is None:
            return queryset
        return queryset.filter(has_whatsapp=value)

    def filter_has_pending_requests(self, queryset, name, value):
        if value is None:
            return queryset
        return queryset.filter(pending_requests_count__gt=0) if value else queryset.filter(pending_requests_count=0)


class DashboardGroupViewSet(StandardModelViewSet):
    """``GET dashboard/groups/``.

    Filters: ``university``, ``faculty``, ``major``, ``academic_year``, ``semester``, ``subject``, ``is_active``,
    ``kind`` (subject|faculty|general), ``requires_approval``, ``has_whatsapp``, ``send_messages_permission``
    (all_members|admins_only), ``has_pending_requests``, ``members_min``/``members_max`` (approved members),
    ``created_after``/``created_before`` (inclusive YYYY-MM-DD), ``search`` (name, description).
    ``ordering``: ``name``, ``created_at``, ``members_count``, ``pending_requests_count`` (``-`` = descending).
    """

    permission_classes = [CanManageGroups]
    serializer_class = DashboardGroupSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = DashboardGroupFilter
    search_fields = ["name", "description"]
    ordering_fields = ["name", "created_at", "members_count", "pending_requests_count"]

    def get_queryset(self):
        queryset = (
            Group.objects.filter(is_deleted=False)
            .select_related("university", "faculty", "major", "academic_year", "semester", "subject", "created_by")
            .annotate(
                members_count=count_subquery(_approved_memberships(), "pk", group_field="group_id"),
                pending_requests_count=count_subquery(_pending_memberships(), "pk", group_field="group_id"),
                has_whatsapp=Exists(_whatsapp_channels().filter(group=OuterRef("pk"))),
            )
            .prefetch_related(
                Prefetch(
                    "external_channels",
                    queryset=GroupExternalChannel.objects.filter(is_active=True, is_deleted=False),
                    to_attr="active_external_channels",
                )
            )
        )
        if not getattr(self, "swagger_fake_view", False) and self.request.user.is_authenticated:
            # the serializer reports the caller's own membership; prefetch it once instead of three queries per row
            queryset = queryset.prefetch_related(
                Prefetch(
                    "memberships",
                    queryset=GroupMembership.objects.filter(user=self.request.user, is_deleted=False),
                    to_attr="current_memberships",
                )
            )
        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class DashboardGroupMembersExportView(APIView):
    """``GET dashboard/groups/<id>/members-export/``: approved members with phone numbers (``?status=all`` for everyone).

    JSON by default, ``?format=csv`` for a UTF-8 (BOM) spreadsheet. Handing out phone numbers is audited.
    """

    permission_classes = [CanManageGroups]
    content_negotiation_class = IgnoreClientContentNegotiation

    @extend_schema(
        tags=["Dashboard"],
        parameters=[
            OpenApiParameter("format", str, enum=["json", "csv"], required=False),
            OpenApiParameter("status", str, enum=["approved", "all"], required=False),
        ],
        responses={(200, "application/json"): OpenApiTypes.OBJECT, (200, "text/csv"): OpenApiTypes.BINARY},
    )
    def get(self, request, pk: int):
        from apps.universities.terms import csv_phone, csv_safe

        group = get_object_or_404(Group, pk=pk, is_deleted=False)
        memberships = GroupMembership.objects.filter(group=group, is_deleted=False).select_related("user")
        if request.query_params.get("status", "approved") != "all":
            memberships = memberships.filter(status=GroupMembershipStatus.APPROVED)
        rows = [
            {
                "user_id": m.user_id,
                "full_name": m.user.full_name,
                "phone_number": m.user.phone_number,
                "role": m.role,
                "status": m.status,
                "joined_at": m.joined_at,
            }
            for m in memberships.order_by("user__full_name", "user_id")
        ]
        wants_csv = request.query_params.get("format", "").lower() == "csv"
        AuditLogService.log(
            actor=request.user,
            action=AuditAction.GROUP_MEMBERS_EXPORTED,
            target=group,
            new_value={"format": "csv" if wants_csv else "json", "members": len(rows)},
            request=request,
        )
        if not wants_csv:
            return success_response(
                data={"group": {"id": group.pk, "name": group.name}, "members": rows}, request=request
            )
        buffer = io.StringIO()
        buffer.write("\ufeff")  # Excel opens UTF-8 (Arabic) correctly only with a BOM
        writer = csv.writer(buffer)
        writer.writerow(["group", "name", "phone", "role", "status", "joined_at"])
        for row in rows:
            writer.writerow(
                [
                    csv_safe(group.name),
                    csv_safe(row["full_name"]),
                    csv_phone(row["phone_number"]),
                    row["role"],
                    row["status"],
                    row["joined_at"].isoformat() if isinstance(row["joined_at"], datetime) else "",
                ]
            )
        response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="group-{group.pk}-members.csv"'
        return response


class GroupJoinView(APIView):
    """Retired: groups are joined automatically when a subject-registration request is approved.

    The route stays so old app versions get a clear, stable error (403 ``GROUP_JOIN_ENROLLMENT_ONLY``) instead of 404.
    """

    permission_classes = [IsStudent]
    serializer_class = GroupMembershipSerializer

    @extend_schema(
        tags=["Groups"],
        request=None,
        deprecated=True,
        description="Always refused with 403 `GROUP_JOIN_ENROLLMENT_ONLY`: students join groups automatically after "
        "their subject-registration request is approved.",
        responses={403: OpenApiResponse(description="GROUP_JOIN_ENROLLMENT_ONLY")},
    )
    def post(self, request, pk: int):
        raise GroupJoinEnrollmentOnly()


class GroupLeaveView(APIView):
    permission_classes = [IsVerifiedStudent]
    serializer_class = GroupMembershipSerializer

    @extend_schema(tags=["Groups"])
    def post(self, request, pk: int):
        def write():
            group = get_object_or_404(Group, pk=pk, is_deleted=False)
            GroupMembershipService.leave(request.user, group)
            return success_response(message="Left group successfully", request=request, code="GROUP_LEFT")

        return run_idempotent(request, f"group-leave:{pk}", write)


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

    @extend_schema(
        tags=["Dashboard"], request=GroupMembershipRoleUpdateSerializer, responses={200: GroupMembershipSerializer}
    )
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


class ExternalChannelLinkRetired(APIException):
    """The ticket + redirect flow ended in an HTTP 302 whose Location header WAS the https invite link: anyone holding a ticket could
    read it with a plain HTTP client. It is permanently retired; clients use ``whatsapp-launch`` (app deep link, members only)."""

    status_code = status.HTTP_410_GONE
    default_detail = "This endpoint was retired. Open the group from the app."
    default_code = "external_channel_link_retired"
    api_code = "EXTERNAL_CHANNEL_LINK_RETIRED"


class GroupWhatsAppTicketView(APIView):
    permission_classes = [IsVerifiedStudent]
    throttle_classes = [ExternalChannelRateThrottle]
    serializer_class = ExternalChannelTicketSerializer

    @extend_schema(
        tags=["Groups"], request=None, responses={410: OpenApiResponse(description="Retired")}, deprecated=True
    )
    def post(self, request, pk: int):
        raise ExternalChannelLinkRetired()


class GroupWhatsAppLaunchView(APIView):
    """Web clients: returns the ``whatsapp://`` deep link so the app opens straight into the group/chat.

    The https invite link is never returned, shown or put in a URL: the client navigates to the app scheme, so no link ever
    appears in the address bar, history or search. Each call is authorised, throttled and audited.
    """

    permission_classes = [IsVerifiedStudent]
    throttle_classes = [ExternalChannelRateThrottle]
    serializer_class = ExternalChannelAppLinkSerializer

    @extend_schema(tags=["Groups"], request=None, responses={200: ExternalChannelAppLinkSerializer})
    def post(self, request, pk: int):
        group = get_object_or_404(Group, pk=pk, is_active=True, is_deleted=False)
        app_url = ExternalChannelService.issue_app_link(group, request.user)
        AuditLogService.log(
            actor=request.user,
            action=AuditAction.GROUP_EXTERNAL_CHANNEL_OPENED,
            target=group,
            new_value={"channel_type": "whatsapp", "mode": "app"},
            request=request,
        )
        response = success_response(
            data={"app_url": app_url},
            message="WhatsApp app link issued",
            request=request,
            code="EXTERNAL_CHANNEL_APP_LINK_ISSUED",
        )
        response["Cache-Control"] = "no-store, max-age=0"
        response["Pragma"] = "no-cache"
        response["Referrer-Policy"] = "no-referrer"
        return response


class ExternalChannelRedirectView(APIView):
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        auth=[], tags=["Protected Assets"], responses={302: OpenApiResponse(description="One-time redirect")}
    )
    def get(self, request, token):
        # Never looks the ticket up and never decrypts the channel: there is nothing left to redirect to.
        raise ExternalChannelLinkRetired()
