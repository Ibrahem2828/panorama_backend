from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    ApproveMembershipView,
    AvailableGroupViewSet,
    BlockMembershipView,
    DashboardGroupMembershipViewSet,
    DashboardGroupViewSet,
    DashboardWhatsAppChannelView,
    ExternalChannelRedirectView,
    GroupDetailViewSet,
    GroupJoinView,
    GroupLeaveView,
    GroupWhatsAppTicketView,
    MembershipRoleUpdateView,
    MyGroupViewSet,
    RejectMembershipView,
)

mobile_router = DefaultRouter()
mobile_router.register("groups/available", AvailableGroupViewSet, basename="groups-available")
mobile_router.register("groups/my", MyGroupViewSet, basename="groups-my")
mobile_router.register("groups", GroupDetailViewSet, basename="groups")

dashboard_router = DefaultRouter()
dashboard_router.register("dashboard/groups", DashboardGroupViewSet, basename="dashboard-groups")

urlpatterns = [
    *mobile_router.urls,
    path("groups/<int:pk>/join/", GroupJoinView.as_view(), name="group-join"),
    path("groups/<int:pk>/leave/", GroupLeaveView.as_view(), name="group-leave"),
    path("groups/<int:pk>/whatsapp-ticket/", GroupWhatsAppTicketView.as_view(), name="group-whatsapp-ticket"),
    path("external-channels/open/<uuid:token>/", ExternalChannelRedirectView.as_view(), name="external-channel-open"),
    *dashboard_router.urls,
    path(
        "dashboard/groups/<int:group_pk>/memberships/",
        DashboardGroupMembershipViewSet.as_view({"get": "list"}),
        name="dashboard-group-memberships",
    ),
    path(
        "dashboard/groups/<int:group_pk>/join-requests/",
        DashboardGroupMembershipViewSet.as_view({"get": "list"}, only_pending=True),
        name="dashboard-group-join-requests",
    ),
    path("dashboard/groups/<int:pk>/whatsapp-channel/", DashboardWhatsAppChannelView.as_view(), name="dashboard-group-whatsapp-channel"),
    path("dashboard/group-memberships/<int:pk>/approve/", ApproveMembershipView.as_view(), name="membership-approve"),
    path("dashboard/group-memberships/<int:pk>/reject/", RejectMembershipView.as_view(), name="membership-reject"),
    path("dashboard/group-memberships/<int:pk>/block/", BlockMembershipView.as_view(), name="membership-block"),
    path("dashboard/group-memberships/<int:pk>/role/", MembershipRoleUpdateView.as_view(), name="membership-role-update"),
]
