from __future__ import annotations

import re

from celery import shared_task
from django.conf import settings
from django.core.cache import cache

from .models import FeedbackAITriage, FeedbackAITriageStatus, FeedbackKind, FeedbackPriority

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<!\\w)\\+?[0-9][0-9 -]{6,}[0-9](?!\\w)")
_TOKEN_RE = re.compile(r"(?i)(bearer|token|otp|password)\\s*[:=]\\s*[^\\s]+")
_URL_RE = re.compile(r"https?://[^\\s]+")


def redact_feedback_text(value: str) -> str:
    """Remove common PII and credentials before any triage provider sees text."""

    value = _EMAIL_RE.sub("[redacted-email]", value)
    value = _PHONE_RE.sub("[redacted-phone]", value)
    value = _TOKEN_RE.sub("[redacted-secret]", value)
    return _URL_RE.sub("[redacted-url]", value)


def _local_classification(feedback) -> dict[str, str | float]:
    text = " ".join([feedback.title, feedback.comment, feedback.suggestion]).lower()
    if feedback.kind == FeedbackKind.BUG or any(word in text for word in ("crash", "error", "fail")):
        topic, sentiment, priority = "bug", "negative", FeedbackPriority.HIGH
    elif feedback.kind == FeedbackKind.COMPLAINT:
        topic, sentiment, priority = "complaint", "negative", FeedbackPriority.HIGH
    elif feedback.kind == FeedbackKind.SUGGESTION:
        topic, sentiment, priority = "product_suggestion", "neutral", FeedbackPriority.NORMAL
    else:
        topic, sentiment, priority = feedback.context, "neutral", FeedbackPriority.NORMAL
    return {"topic": topic, "sentiment": sentiment, "priority": priority, "confidence": 0.5}


@shared_task(bind=True, autoretry_for=(TimeoutError,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def triage_feedback(self, feedback_id: int) -> str:
    """Produce review-only triage; submission never depends on this task."""

    if not getattr(settings, "FEEDBACK_AI_TRIAGE_ENABLED", False):
        return "disabled"
    from .models import AppFeedback

    feedback = AppFeedback.objects.filter(pk=feedback_id, is_deleted=False).first()
    if not feedback:
        return "missing"
    circuit_key = "feedback:triage:circuit-open"
    triage, _ = FeedbackAITriage.objects.get_or_create(feedback=feedback)
    if cache.get(circuit_key):
        triage.status = FeedbackAITriageStatus.SKIPPED
        triage.failure_reason = "circuit_open"
        triage.save(update_fields=["status", "failure_reason", "updated_at"])
        return "circuit_open"

    try:
        redacted = redact_feedback_text("\\n".join([feedback.title, feedback.comment, feedback.suggestion]))
        provider = getattr(settings, "FEEDBACK_AI_PROVIDER", "local_safe_heuristic")
        if provider != "local_safe_heuristic":
            triage.status = FeedbackAITriageStatus.SKIPPED
            triage.provider = provider
            triage.failure_reason = "provider_not_configured"
            triage.redacted_text = redacted
            triage.save()
            return "provider_not_configured"
        result = _local_classification(feedback)
        triage.status = FeedbackAITriageStatus.COMPLETED
        triage.provider = provider
        triage.model = "rules"
        triage.model_version = "1"
        triage.redacted_text = redacted
        triage.topic = result["topic"]
        triage.sentiment = result["sentiment"]
        triage.suggested_priority = result["priority"]
        triage.confidence = result["confidence"]
        triage.failure_reason = ""
        triage.save()
        return "completed"
    except Exception:
        cache.set(circuit_key, True, timeout=getattr(settings, "FEEDBACK_AI_CIRCUIT_SECONDS", 300))
        triage.status = FeedbackAITriageStatus.FAILED
        triage.failure_reason = "triage_failed"
        triage.save(update_fields=["status", "failure_reason", "updated_at"])
        raise
