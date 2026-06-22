from django.urls import path
from rest_framework.routers import DefaultRouter

from .student_account_request_views import (
    DashboardStudentAccountRequestViewSet,
    StudentAccountRequestApproveView,
    StudentAccountRequestCardPreviewTokenView,
    StudentAccountRequestNeedsUpdateView,
    StudentAccountRequestRejectView,
    StudentAccountRequestResendOtpView,
)

dashboard_router = DefaultRouter()
dashboard_router.register(
    "dashboard/student-account-requests",
    DashboardStudentAccountRequestViewSet,
    basename="dashboard-student-account-requests",
)

urlpatterns = [
    *dashboard_router.urls,
    path(
        "dashboard/student-account-requests/<int:pk>/approve/",
        StudentAccountRequestApproveView.as_view(),
        name="student-account-request-approve",
    ),
    path(
        "dashboard/student-account-requests/<int:pk>/reject/",
        StudentAccountRequestRejectView.as_view(),
        name="student-account-request-reject",
    ),
    path(
        "dashboard/student-account-requests/<int:pk>/needs-update/",
        StudentAccountRequestNeedsUpdateView.as_view(),
        name="student-account-request-needs-update",
    ),
    path(
        "dashboard/student-account-requests/<int:pk>/resend-otp/",
        StudentAccountRequestResendOtpView.as_view(),
        name="student-account-request-resend-otp",
    ),
    path(
        "dashboard/student-account-requests/<int:pk>/card-preview-token/",
        StudentAccountRequestCardPreviewTokenView.as_view(),
        name="student-account-request-card-preview-token",
    ),
]