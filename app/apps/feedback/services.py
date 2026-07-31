from __future__ import annotations

import hashlib
import re
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Avg, Count
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.notifications.models import NotificationType
from apps.notifications.services import NotificationService

from .models import (
    AppFeedback,
    FeedbackKind,
    FeedbackMetricType,
    FeedbackPromptEvent,
    FeedbackPromptPolicy,
    FeedbackStatus,
    FeedbackVote,
)

SENSITIVE_METADATA_KEYS = {
    "password",
    "token",
    "access",
    "refresh",
    "authorization",
    "otp",
    "code",
    "secret",
    "card_image",
    "file_url",
}


def _feedback_text(validated_data: dict) -> str:
    return " ".join(
        str(validated_data.get(field, "")).strip().lower()
        for field in ("title", "comment", "suggestion")
        if str(validated_data.get(field, "")).strip()
    )


def content_fingerprint(validated_data: dict) -> str:
    payload = "|".join(
        [
            str(validated_data.get("kind", "")),
            str(validated_data.get("context", "")),
            str(validated_data.get("action_key", "")),
            _feedback_text(validated_data),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def abuse_flags(validated_data: dict) -> list[str]:
    text = _feedback_text(validated_data)
    flags: list[str] = []
    if re.search(r"(.)\\1{12,}", text):
        flags.append("repeated_characters")
    if re.search(r"https?://", text):
        flags.append("contains_url")
    terms = [term.strip().lower() for term in getattr(settings, "FEEDBACK_ABUSE_TERMS", []) if term.strip()]
    if any(term in text for term in terms):
        flags.append("configured_abuse_term")
    return flags


def validate_metadata(value, path="metadata"):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValidationError({path: "Metadata must be a JSON object."})
    if len(str(value)) > 10_000:
        raise ValidationError({path: "Metadata is too large."})
    for key, item in value.items():
        if str(key).lower() in SENSITIVE_METADATA_KEYS:
            raise ValidationError({path: f"Sensitive key '{key}' is not allowed."})
        if isinstance(item, dict):
            validate_metadata(item, path)
        elif isinstance(item, list):
            if len(item) > 50:
                raise ValidationError({path: "Metadata lists cannot contain more than 50 items."})
            for list_item in item:
                if isinstance(list_item, dict):
                    validate_metadata(list_item, path)
    return value


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = []
    for part in str(value or "").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts or [0])


class FeedbackPromptService:
    @staticmethod
    def eligible(
        user,
        context: str,
        action_key: str = "",
        app_version: str = "",
        platform: str = "",
        locale: str = "",
    ) -> FeedbackPromptPolicy | None:
        policy = FeedbackPromptPolicy.objects.filter(
            context=context,
            action_key=action_key,
            is_active=True,
            is_deleted=False,
        ).first()
        if not policy and action_key:
            policy = FeedbackPromptPolicy.objects.filter(
                context=context,
                action_key="",
                is_active=True,
                is_deleted=False,
            ).first()
        if not policy:
            return None
        if policy.minimum_app_version and _version_tuple(app_version) < _version_tuple(policy.minimum_app_version):
            return None
        if policy.maximum_app_version and _version_tuple(app_version) > _version_tuple(policy.maximum_app_version):
            return None
        if policy.platforms and platform not in policy.platforms:
            return None
        if policy.locales and locale not in policy.locales:
            return None
        if policy.roles and user.role not in policy.roles:
            return None
        verification_status = getattr(getattr(user, "student_profile", None), "verification_status", "")
        if policy.verification_statuses and verification_status not in policy.verification_statuses:
            return None
        sample_seed = f"{user.pk}:{policy.pk}"
        bucket = int(hashlib.sha256(sample_seed.encode("utf-8")).hexdigest()[:8], 16) % 100
        if bucket >= policy.sample_percent:
            return None
        cooldown_since = timezone.now() - timedelta(days=policy.cooldown_days)
        recently_prompted = FeedbackPromptEvent.objects.filter(
            user=user,
            policy=policy,
            created_at__gte=cooldown_since,
            event__in=["shown", "submitted"],
            is_deleted=False,
        ).exists()
        if recently_prompted:
            return None
        dismissed_since = timezone.now() - timedelta(days=policy.dismiss_cooldown_days)
        if FeedbackPromptEvent.objects.filter(
            user=user,
            policy=policy,
            event="dismissed",
            created_at__gte=dismissed_since,
            is_deleted=False,
        ).exists():
            return None
        prompts_since = timezone.now() - timedelta(days=30)
        prompt_count = FeedbackPromptEvent.objects.filter(
            user=user,
            policy=policy,
            event__in=["shown", "submitted"],
            created_at__gte=prompts_since,
            is_deleted=False,
        ).count()
        return policy if prompt_count < policy.max_prompts_per_30_days else None


class FeedbackService:
    @staticmethod
    @transaction.atomic
    def submit(user, validated_data: dict, request=None) -> AppFeedback:
        user = user.__class__.objects.select_for_update().get(pk=user.pk)
        metadata = validate_metadata(validated_data.pop("metadata", {}))
        validated_data["metadata"] = metadata
        kind = validated_data["kind"]
        metric_type = validated_data.get("metric_type") or (
            FeedbackMetricType.STARS
            if kind == FeedbackKind.RATING or validated_data.get("rating") is not None
            else FeedbackMetricType.FREE_TEXT
        )
        validated_data["metric_type"] = metric_type
        metric_value = validated_data.get("metric_value")
        rating = validated_data.get("rating")
        if metric_value is None and metric_type == FeedbackMetricType.STARS:
            metric_value = rating
        ranges = {
            FeedbackMetricType.CSAT: (1, 5),
            FeedbackMetricType.CES: (1, 7),
            FeedbackMetricType.NPS: (0, 10),
            FeedbackMetricType.STARS: (1, 5),
        }
        if metric_type in ranges:
            minimum, maximum = ranges[metric_type]
            if metric_value is None or not minimum <= metric_value <= maximum:
                raise ValidationError({"metric_value": f"{metric_type} must be between {minimum} and {maximum}."})
            validated_data["metric_value"] = metric_value
            validated_data["rating"] = metric_value if metric_type == FeedbackMetricType.STARS else None
        elif metric_type == FeedbackMetricType.FREE_TEXT:
            if metric_value is not None:
                raise ValidationError({"metric_value": "free_text feedback cannot include a numeric value."})
            validated_data["rating"] = None
        if kind == FeedbackKind.RATING and metric_type == FeedbackMetricType.FREE_TEXT:
            raise ValidationError({"metric_type": "A rating cannot use the free_text metric."})
        if kind != FeedbackKind.RATING and not any(
            str(validated_data.get(field, "")).strip() for field in ("title", "comment", "suggestion")
        ):
            raise ValidationError({"comment": "Please provide feedback details."})

        fingerprint = content_fingerprint(validated_data)
        validated_data["content_fingerprint"] = fingerprint
        validated_data["abuse_flags"] = abuse_flags(validated_data)
        if kind != FeedbackKind.RATING:
            duplicate = AppFeedback.objects.filter(
                user=user,
                content_fingerprint=fingerprint,
                created_at__gte=timezone.now() - timedelta(minutes=10),
                is_deleted=False,
            ).first()
            if duplicate:
                return duplicate

        if kind == FeedbackKind.RATING:
            lookup = {
                "user": user,
                "kind": FeedbackKind.RATING,
                "context": validated_data["context"],
                "action_key": validated_data.get("action_key", ""),
                "object_type": validated_data.get("object_type", ""),
                "object_id": validated_data.get("object_id", ""),
                "app_version": validated_data.get("app_version", ""),
                "is_deleted": False,
            }
            defaults = dict(validated_data)
            defaults.pop("kind", None)
            feedback, _ = AppFeedback.objects.update_or_create(defaults=defaults, **lookup)
        else:
            feedback = AppFeedback.objects.create(user=user, **validated_data)

        policy = FeedbackPromptPolicy.objects.filter(
            context=feedback.context,
            action_key=feedback.action_key,
            is_active=True,
            is_deleted=False,
        ).first()
        if policy:
            FeedbackPromptEvent.objects.create(
                user=user,
                policy=policy,
                event="submitted",
                app_version=feedback.app_version,
            )
        AuditLogService.log(
            actor=user,
            action=AuditAction.FEEDBACK_SUBMITTED,
            target=feedback,
            new_value={"kind": feedback.kind, "context": feedback.context, "rating": feedback.rating},
            request=request,
        )
        if getattr(settings, "FEEDBACK_AI_TRIAGE_ENABLED", False):
            from .tasks import triage_feedback

            transaction.on_commit(lambda feedback_id=feedback.pk: triage_feedback.delay(feedback_id))
        return feedback

    @staticmethod
    @transaction.atomic
    def toggle_vote(user, feedback: AppFeedback) -> tuple[bool, int]:
        if (
            feedback.kind != FeedbackKind.SUGGESTION
            or feedback.is_deleted
            or feedback.status not in {FeedbackStatus.PLANNED, FeedbackStatus.IN_PROGRESS, FeedbackStatus.RESOLVED}
        ):
            raise ValidationError("Only published suggestions can be voted on.")
        vote = FeedbackVote.objects.filter(feedback=feedback, user=user).first()
        if vote:
            vote.delete()
            voted = False
        else:
            FeedbackVote.objects.create(feedback=feedback, user=user)
            voted = True
        return voted, feedback.votes.count()

    @staticmethod
    def analytics(queryset=None) -> dict:
        queryset = queryset or AppFeedback.objects.filter(is_deleted=False)
        ratings = queryset.filter(kind=FeedbackKind.RATING, rating__isnull=False)
        csat = queryset.filter(metric_type=FeedbackMetricType.CSAT, metric_value__isnull=False)
        ces = queryset.filter(metric_type=FeedbackMetricType.CES, metric_value__isnull=False)
        nps = queryset.filter(metric_type=FeedbackMetricType.NPS, metric_value__isnull=False)
        total_ratings = ratings.count()
        distribution = {
            str(row["rating"]): row["count"]
            for row in ratings.values("rating").annotate(count=Count("id")).order_by("rating")
        }
        by_context = list(
            ratings.values("context").annotate(average=Avg("rating"), count=Count("id")).order_by("context")
        )
        satisfied = ratings.filter(rating__gte=4).count()
        nps_total = nps.count()
        prompt_events = FeedbackPromptEvent.objects.filter(is_deleted=False)
        shown_count = prompt_events.filter(event="shown").count()
        submitted_count = prompt_events.filter(event="submitted").count()
        return {
            "total_feedback": queryset.count(),
            "total_ratings": total_ratings,
            "average_rating": ratings.aggregate(value=Avg("rating"))["value"],
            "satisfaction_percent": round((satisfied / total_ratings) * 100, 2) if total_ratings else 0,
            "csat": {
                "average": csat.aggregate(value=Avg("metric_value"))["value"],
                "distribution": {
                    str(row["metric_value"]): row["count"]
                    for row in csat.values("metric_value").annotate(count=Count("id")).order_by("metric_value")
                },
            },
            "ces": {"average": ces.aggregate(value=Avg("metric_value"))["value"], "responses": ces.count()},
            "nps": {
                "score": round(
                    ((nps.filter(metric_value__gte=9).count() - nps.filter(metric_value__lte=6).count()) / nps_total)
                    * 100,
                    2,
                )
                if nps_total
                else None,
                "promoters": nps.filter(metric_value__gte=9).count(),
                "passives": nps.filter(metric_value__gte=7, metric_value__lte=8).count(),
                "detractors": nps.filter(metric_value__lte=6).count(),
            },
            "prompt_conversion_percent": round((submitted_count / shown_count) * 100, 2) if shown_count else 0,
            "prompt_events": {"shown": shown_count, "submitted": submitted_count},
            "rating_distribution": distribution,
            "ratings_by_context": by_context,
            "trends_by_version_platform": list(
                queryset.values("app_version", "platform")
                .annotate(count=Count("id"), average_metric=Avg("metric_value"))
                .order_by("app_version", "platform")[:100]
            ),
            "open_items": queryset.exclude(
                status__in=[FeedbackStatus.RESOLVED, FeedbackStatus.REJECTED, FeedbackStatus.DUPLICATE]
            ).count(),
            "critical_items": queryset.filter(priority="critical").exclude(status=FeedbackStatus.RESOLVED).count(),
            "top_suggestions": list(
                queryset.filter(kind=FeedbackKind.SUGGESTION)
                .annotate(votes_count=Count("votes"))
                .values("id", "title", "suggestion", "status", "votes_count")
                .order_by("-votes_count", "-created_at")[:10]
            ),
        }


class FeedbackWorkflowService:
    VALID_TRANSITIONS = {
        FeedbackStatus.NEW: {
            FeedbackStatus.REVIEWING,
            FeedbackStatus.PLANNED,
            FeedbackStatus.IN_PROGRESS,
            FeedbackStatus.RESOLVED,
            FeedbackStatus.REJECTED,
            FeedbackStatus.DUPLICATE,
        },
        FeedbackStatus.REVIEWING: {
            FeedbackStatus.PLANNED,
            FeedbackStatus.IN_PROGRESS,
            FeedbackStatus.RESOLVED,
            FeedbackStatus.REJECTED,
            FeedbackStatus.DUPLICATE,
        },
        FeedbackStatus.PLANNED: {
            FeedbackStatus.IN_PROGRESS,
            FeedbackStatus.RESOLVED,
            FeedbackStatus.REJECTED,
            FeedbackStatus.DUPLICATE,
        },
        FeedbackStatus.IN_PROGRESS: {
            FeedbackStatus.PLANNED,
            FeedbackStatus.RESOLVED,
            FeedbackStatus.REJECTED,
            FeedbackStatus.DUPLICATE,
        },
        FeedbackStatus.RESOLVED: {FeedbackStatus.REVIEWING},
        FeedbackStatus.REJECTED: {FeedbackStatus.REVIEWING},
        FeedbackStatus.DUPLICATE: {FeedbackStatus.REVIEWING},
    }

    @classmethod
    @transaction.atomic
    def update(cls, feedback, actor, validated_data: dict, request=None):
        feedback = AppFeedback.objects.select_for_update().get(pk=feedback.pk, is_deleted=False)
        new_status = validated_data["status"]
        if new_status != feedback.status and new_status not in cls.VALID_TRANSITIONS.get(feedback.status, set()):
            raise ValidationError({"status": f"Invalid transition from {feedback.status} to {new_status}."})
        old = {
            "status": feedback.status,
            "priority": feedback.priority,
            "assigned_to": feedback.assigned_to_id,
        }
        for field, value in validated_data.items():
            setattr(feedback, field, value)
        feedback.save()
        AuditLogService.log(
            actor=actor,
            action=AuditAction.FEEDBACK_WORKFLOW_UPDATED,
            target=feedback,
            old_value=old,
            new_value={
                "status": feedback.status,
                "priority": feedback.priority,
                "assigned_to": feedback.assigned_to_id,
            },
            request=request,
        )
        if old["status"] != feedback.status:
            body = feedback.resolution_message.strip() or f"تم تحديث حالة ملاحظتك إلى: {feedback.get_status_display()}."
            NotificationService.create_notification(
                feedback.user,
                "تحديث على تقييمك أو اقتراحك",
                body,
                type=NotificationType.FEEDBACK,
                data={"feedback_id": feedback.id, "status": feedback.status},
            )
        return feedback
