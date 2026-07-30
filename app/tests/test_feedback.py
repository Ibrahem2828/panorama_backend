import pytest
from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.feedback.models import (
    AppFeedback,
    FeedbackContext,
    FeedbackKind,
    FeedbackMetricType,
    FeedbackPromptPolicy,
    FeedbackStatus,
)
from apps.feedback.services import FeedbackPromptService, FeedbackService, FeedbackWorkflowService
from apps.feedback.tasks import triage_feedback
from rest_framework.exceptions import ValidationError


@pytest.mark.django_db
def test_rating_is_upserted_per_action_object_version():
    user = User.objects.create_user(
        email="rater@example.com",
        phone_number="+963900000003",
        password="A-Strong-Test-Password-123!",
        full_name="Rater",
        role=UserRole.STUDENT,
        is_email_verified=True,
    )
    base = {
        "kind": FeedbackKind.RATING,
        "context": FeedbackContext.PRINTING,
        "action_key": "printing.order.delivered",
        "object_type": "print_order",
        "object_id": "44",
        "app_version": "2.0.0",
        "rating": 3,
        "metadata": {},
    }
    first = FeedbackService.submit(user, dict(base))
    updated = FeedbackService.submit(user, {**base, "rating": 5, "comment": "Improved"})
    assert first.pk == updated.pk
    assert AppFeedback.objects.filter(user=user, kind=FeedbackKind.RATING).count() == 1
    updated.refresh_from_db()
    assert updated.rating == 5


@pytest.mark.django_db
def test_feedback_terminal_transition_notifies_user():
    user = User.objects.create_user(
        email="feedback@example.com",
        phone_number="+963900000004",
        password="A-Strong-Test-Password-123!",
        full_name="Feedback User",
        role=UserRole.NORMAL_USER,
        is_email_verified=True,
    )
    admin = User.objects.create_user(
        email="admin@example.com",
        phone_number="+963900000005",
        password="A-Strong-Test-Password-123!",
        full_name="Admin",
        role=UserRole.ADMIN,
        is_email_verified=True,
    )
    feedback = AppFeedback.objects.create(
        user=user,
        kind=FeedbackKind.SUGGESTION,
        context=FeedbackContext.APP,
        title="Better search",
        suggestion="Add filters",
    )
    result = FeedbackWorkflowService.update(
        feedback,
        admin,
        {
            "status": FeedbackStatus.RESOLVED,
            "resolution_message": "تم تنفيذ الاقتراح في النسخة الجديدة.",
        },
    )
    assert result.status == FeedbackStatus.RESOLVED
    assert user.notifications.filter(data__feedback_id=feedback.id).exists()


@pytest.mark.django_db
def test_feedback_metrics_deduplication_prompt_policy_and_safe_triage(settings):
    user = User.objects.create_user(
        email="metric@example.com",
        phone_number="+963900000006",
        password="A-Strong-Test-Password-123!",
        full_name="Metric User",
        role=UserRole.NORMAL_USER,
        is_email_verified=True,
    )
    csat = FeedbackService.submit(
        user,
        {
            "kind": FeedbackKind.COMPLAINT,
            "metric_type": FeedbackMetricType.CSAT,
            "metric_value": 5,
            "context": FeedbackContext.SUPPORT,
            "comment": "Helpful team",
        },
    )
    assert csat.metric_value == 5
    assert csat.rating is None

    payload = {
        "kind": FeedbackKind.SUGGESTION,
        "context": FeedbackContext.SEARCH,
        "title": "Better search",
        "suggestion": "Please add filters",
    }
    first = FeedbackService.submit(user, payload)
    duplicate = FeedbackService.submit(user, dict(payload))
    assert first.pk == duplicate.pk
    assert AppFeedback.objects.filter(user=user, kind=FeedbackKind.SUGGESTION).count() == 1

    with pytest.raises(ValidationError):
        FeedbackService.submit(user, {**payload, "metric_type": FeedbackMetricType.NPS, "metric_value": 11})

    policy = FeedbackPromptPolicy.objects.create(
        context=FeedbackContext.SEARCH,
        title="How was search?",
        question="Please rate search",
        sample_percent=100,
        max_prompts_per_30_days=1,
        platforms=["android"],
    )
    assert FeedbackPromptService.eligible(user, FeedbackContext.SEARCH, platform="android") == policy
    assert FeedbackPromptService.eligible(user, FeedbackContext.SEARCH, platform="ios") is None

    settings.FEEDBACK_AI_TRIAGE_ENABLED = True
    triage_feedback.run(first.id)
    first.refresh_from_db()
    assert first.ai_triage.status == "completed"
