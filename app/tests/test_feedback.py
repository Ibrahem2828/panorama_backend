import pytest

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.feedback.models import AppFeedback, FeedbackContext, FeedbackKind, FeedbackStatus
from apps.feedback.services import FeedbackService, FeedbackWorkflowService


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
