from django.urls import path

from .views import (
    CancelEnrollmentView,
    DashboardEnrollmentViewSet,
    EnrollmentBulkDecisionView,
    EnrollmentDecisionView,
    EnrollmentScreenshotStreamView,
    EnrollmentScreenshotTicketView,
    EnrollmentSummaryView,
    MyEnrollmentsView,
    SubjectSearchView,
)

request_list = DashboardEnrollmentViewSet.as_view({"get": "list"})
request_detail = DashboardEnrollmentViewSet.as_view({"get": "retrieve"})

urlpatterns = [
    path("enrollment/subjects/", SubjectSearchView.as_view(), name="enrollment-subject-search"),
    path("enrollment/requests/", MyEnrollmentsView.as_view(), name="enrollment-requests"),
    path("enrollment/requests/<int:pk>/cancel/", CancelEnrollmentView.as_view(), name="enrollment-cancel"),
    path("dashboard/enrollment/requests/", request_list, name="dashboard-enrollment-requests"),
    path(
        "dashboard/enrollment/requests/bulk-decision/",
        EnrollmentBulkDecisionView.as_view(),
        name="dashboard-enrollment-bulk",
    ),
    path("dashboard/enrollment/requests/<int:pk>/", request_detail, name="dashboard-enrollment-request"),
    path(
        "dashboard/enrollment/requests/<int:pk>/decision/",
        EnrollmentDecisionView.as_view(),
        name="dashboard-enrollment-decision",
    ),
    path(
        "dashboard/enrollment/requests/<int:pk>/screenshot-ticket/",
        EnrollmentScreenshotTicketView.as_view(),
        name="dashboard-enrollment-screenshot-ticket",
    ),
    path("dashboard/enrollment/summary/", EnrollmentSummaryView.as_view(), name="dashboard-enrollment-summary"),
    path(
        "enrollment-screenshot-access/<uuid:token>/",
        EnrollmentScreenshotStreamView.as_view(),
        name="enrollment-screenshot-access",
    ),
]
