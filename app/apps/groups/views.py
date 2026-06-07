from django.db.models import Count, Prefetch, Q
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import filters, status
from rest_framework.views import APIView

from apps.accounts.permissions import IsAdminOrITSupport, IsVerifiedStudent
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.responses import success_response
from apps.common.viewsets import StandardModelViewSet, StandardReadOnlyModelViewSet

from .models import Group, GroupMembership, GroupMembershipStatus
from .serializers import GroupMembershipRoleUpdateSerializer, GroupMembershipSerializer, GroupSerializer
from .services import GroupMembershipService, is_student_eligible_for_group


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
        memberships = GroupMembership.objects.filter(user=user)
        queryset = (
            Group.objects.filter(university=profile.university, is_active=True, is_deleted=False)
            .select_related("university", "faculty", "major", "academic_year", "semester", "subject")
            .annotate(members_count=Count("memberships", filter=Q(memberships__status=GroupMembershipStatus.APPROVED)))
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
        return Group.objects.filter(
            memberships__user=self.request.user,
            memberships__status=GroupMembershipStatus.APPROVED,
            is_active=True,
            is_deleted=False,
        ).annotate(members_count=Count("memberships", filter=Q(memberships__status=GroupMembershipStatus.APPROVED))).distinct().order_by("name")


class GroupDetailViewSet(StandardReadOnlyModelViewSet):
    permission_classes = [IsVerifiedStudent]
    serializer_class = GroupSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Group.objects.none()
        return Group.objects.filter(is_active=True, is_deleted=False).select_related(
            "university", "faculty", "major", "academic_year", "semester", "subject"
        ).annotate(members_count=Count("memberships", filter=Q(memberships__status=GroupMembershipStatus.APPROVED)))

    def get_object(self):
        group = super().get_object()
        if not is_student_eligible_for_group(self.request.user, group):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("You are not eligible to view this group.")
        return group


class DashboardGroupViewSet(StandardModelViewSet):
    permission_classes = [IsAdminOrITSupport]
    serializer_class = GroupSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["university", "faculty", "major", "academic_year", "semester", "subject", "is_active"]
    search_fields = ["name", "description"]
    ordering_fields = ["name", "created_at"]

    def get_queryset(self):
        return Group.objects.filter(is_deleted=False).select_related(
            "university", "faculty", "major", "academic_year", "semester", "subject", "created_by"
        ).annotate(members_count=Count("memberships", filter=Q(memberships__status=GroupMembershipStatus.APPROVED)))

    def perform_create(self, serializer):
        group = serializer.save(created_by=self.request.user)
        AuditLogService.log(actor=self.request.user, action=AuditAction.GROUP_CREATED, target=group, request=self.request)

    def perform_update(self, serializer):
        group = serializer.save()
        AuditLogService.log(actor=self.request.user, action=AuditAction.GROUP_UPDATED, target=group, request=self.request)

    def perform_destroy(self, instance):
        AuditLogService.log(actor=self.request.user, action=AuditAction.GROUP_DELETED, target=instance, request=self.request)
        super().perform_destroy(instance)


class GroupJoinView(APIView):
    permission_classes = [IsVerifiedStudent]
    serializer_class = GroupMembershipSerializer

    @extend_schema(tags=["Groups"], responses={200: GroupMembershipSerializer})
    def post(self, request, pk: int):
        group = Group.objects.get(pk=pk)
        membership = GroupMembershipService.join(request.user, group)
        return success_response(data=GroupMembershipSerializer(membership).data, message="Group join request submitted")


class GroupLeaveView(APIView):
    permission_classes = [IsVerifiedStudent]
    serializer_class = GroupMembershipSerializer

    @extend_schema(tags=["Groups"])
    def post(self, request, pk: int):
        group = Group.objects.get(pk=pk)
        GroupMembershipService.leave(request.user, group)
        return success_response(message="Left group successfully")


class DashboardGroupMembershipViewSet(StandardReadOnlyModelViewSet):
    only_pending = False
    permission_classes = [IsAdminOrITSupport]
    serializer_class = GroupMembershipSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["status", "role"]
    search_fields = ["user__full_name", "user__email", "group__name"]
    ordering_fields = ["created_at", "reviewed_at", "joined_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return GroupMembership.objects.none()
        queryset = GroupMembership.objects.filter(group_id=self.kwargs["group_pk"], is_deleted=False).select_related("group", "user")
        if getattr(self, "only_pending", False):
            queryset = queryset.filter(status=GroupMembershipStatus.PENDING)
        return queryset


class MembershipReviewView(APIView):
    permission_classes = [IsAdminOrITSupport]
    serializer_class = GroupMembershipSerializer
    target_status = GroupMembershipStatus.APPROVED

    @extend_schema(tags=["Dashboard"], responses={200: GroupMembershipSerializer})
    def post(self, request, pk: int):
        membership = GroupMembership.objects.select_related("group", "user").get(pk=pk, is_deleted=False)
        membership = GroupMembershipService.review(membership, request.user, self.target_status)
        return success_response(data=GroupMembershipSerializer(membership).data, message="Membership updated successfully", status_code=status.HTTP_200_OK)


class ApproveMembershipView(MembershipReviewView):
    target_status = GroupMembershipStatus.APPROVED


class RejectMembershipView(MembershipReviewView):
    target_status = GroupMembershipStatus.REJECTED


class BlockMembershipView(MembershipReviewView):
    target_status = GroupMembershipStatus.BLOCKED


class MembershipRoleUpdateView(APIView):
    permission_classes = [IsAdminOrITSupport]
    serializer_class = GroupMembershipRoleUpdateSerializer

    @extend_schema(tags=["Dashboard"], request=GroupMembershipRoleUpdateSerializer, responses={200: GroupMembershipSerializer})
    def patch(self, request, pk: int):
        membership = GroupMembership.objects.select_related("group", "user").get(pk=pk, is_deleted=False)
        serializer = GroupMembershipRoleUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        old_role = membership.role
        membership.role = serializer.validated_data["role"]
        membership.save(update_fields=["role", "updated_at"])
        AuditLogService.log(
            actor=request.user,
            action=AuditAction.GROUP_MEMBERSHIP_ROLE_CHANGED,
            target=membership,
            old_value={"role": old_role},
            new_value={"role": membership.role},
            request=request,
        )
        return success_response(data=GroupMembershipSerializer(membership).data, message="Membership role updated successfully")
