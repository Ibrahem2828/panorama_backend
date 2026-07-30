from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    DashboardFeedbackAnalyticsView,
    DashboardFeedbackPromptPolicyViewSet,
    DashboardFeedbackViewSet,
    DashboardFeedbackWorkflowView,
    FeedbackPrivacyRequestView,
    FeedbackPromptEligibilityView,
    FeedbackPromptEventView,
    FeedbackSubmitView,
    FeedbackVoteView,
    MyFeedbackViewSet,
    PublicSuggestionViewSet,
)

router = DefaultRouter()
router.register("feedback/mine", MyFeedbackViewSet, basename="my-feedback")
router.register("feedback/suggestions", PublicSuggestionViewSet, basename="feedback-suggestions")
router.register("dashboard/feedback", DashboardFeedbackViewSet, basename="dashboard-feedback")
router.register("dashboard/feedback-prompt-policies", DashboardFeedbackPromptPolicyViewSet, basename="dashboard-feedback-prompt-policies")

urlpatterns = [
    path("feedback/", FeedbackSubmitView.as_view(), name="feedback-submit"),
    path("feedback/prompt/", FeedbackPromptEligibilityView.as_view(), name="feedback-prompt"),
    path("feedback/prompt-event/", FeedbackPromptEventView.as_view(), name="feedback-prompt-event"),
    path("feedback/<int:pk>/privacy-request/", FeedbackPrivacyRequestView.as_view(), name="feedback-privacy-request"),
    path("feedback/<int:pk>/vote/", FeedbackVoteView.as_view(), name="feedback-vote"),
    *router.urls,
    path("dashboard/feedback/<int:pk>/workflow/", DashboardFeedbackWorkflowView.as_view(), name="dashboard-feedback-workflow"),
    path("dashboard/feedback-analytics/", DashboardFeedbackAnalyticsView.as_view(), name="dashboard-feedback-analytics"),
]
