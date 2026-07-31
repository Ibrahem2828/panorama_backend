from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    ApproveVerificationView,
    DashboardVerificationViewSet,
    MyVerificationView,
    NeedsUpdateVerificationView,
    RejectVerificationView,
    SubmitVerificationView,
    VerificationCardStreamView,
    VerificationCardTicketView,
)

dashboard_router = DefaultRouter()
dashboard_router.register("dashboard/verifications", DashboardVerificationViewSet, basename="dashboard-verifications")

urlpatterns = [
    path("verification/submit/", SubmitVerificationView.as_view(), name="verification-submit"),
    path("verification/resubmit/", SubmitVerificationView.as_view(), name="verification-resubmit"),
    path("verification/me/", MyVerificationView.as_view(), name="verification-me"),
    *dashboard_router.urls,
    path("dashboard/verifications/<int:pk>/approve/", ApproveVerificationView.as_view(), name="verification-approve"),
    path("dashboard/verifications/<int:pk>/reject/", RejectVerificationView.as_view(), name="verification-reject"),
    path(
        "dashboard/verifications/<int:pk>/needs-update/",
        NeedsUpdateVerificationView.as_view(),
        name="verification-needs-update",
    ),
    path(
        "dashboard/verifications/<int:pk>/card-ticket/",
        VerificationCardTicketView.as_view(),
        name="verification-card-ticket",
    ),
    path(
        "verification-card-access/<uuid:token>/", VerificationCardStreamView.as_view(), name="verification-card-access"
    ),
]
