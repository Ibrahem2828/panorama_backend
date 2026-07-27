from __future__ import annotations

import hashlib
from datetime import timedelta

from django.db import transaction
from django.db.models import Avg, Count, Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.notifications.models import NotificationType
from apps.notifications.services import NotificationService

from .models import (
    AppFeedback,
    FeedbackKind,
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
    def eligible(user, context: str, action_key: str = "", app_version: str = "") -> FeedbackPromptPolicy | None:
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
        sample_seed = f"{user.pk}:{policy.pk}:{app_version}"
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
        return None if recently_prompted else policy


class FeedbackService:
    @staticmethod
    @transaction.atomic
    def submit(user, validated_data: dict, request=None) -> AppFeedback:
        metadata = validate_metadata(validated_data.pop("metadata", {}))
        validated_data["metadata"] = metadata
        kind = validated_data["kind"]
        rating = validated_data.get("rating")
        if kind == FeedbackKind.RATING and rating is None:
            raise ValidationError({"rating": "A rating from 1 to 5 is required."})
        if kind != FeedbackKind.RATING and not any(
            str(validated_data.get(field, "")).strip() for field in ("title", "comment", "suggestion")
        ):
            raise ValidationError({"comment": "Please provide feedback details."})

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
        total_ratings = ratings.count()
        distribution = {
            str(row["rating"]): row["count"]
            for row in ratings.values("rating").annotate(count=Count("id")).order_by("rating")
        }
        by_context = list(
            ratings.values("context")
            .annotate(average=Avg("rating"), count=Count("id"))
            .order_by("context")
        )
        satisfied = ratings.filter(rating__gte=4).count()
        return {
            "total_feedback": queryset.count(),
            "total_ratings": total_ratings,
            "average_rating": ratings.aggregate(value=Avg("rating"))["value"],
            "satisfaction_percent": round((satisfied / total_ratings) * 100, 2) if total_ratings else 0,
            "rating_distribution": distribution,
            "ratings_by_context": by_context,
            "open_items": queryset.exclude(status__in=[FeedbackStatus.RESOLVED, FeedbackStatus.REJECTED, FeedbackStatus.DUPLICATE]).count(),
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
        FeedbackStatus.NEW: {FeedbackStatus.REVIEWING, FeedbackStatus.PLANNED, FeedbackStatus.IN_PROGRESS, FeedbackStatus.RESOLVED, FeedbackStatus.REJECTED, FeedbackStatus.DUPLICATE},
        FeedbackStatus.REVIEWING: {FeedbackStatus.PLANNED, FeedbackStatus.IN_PROGRESS, FeedbackStatus.RESOLVED, FeedbackStatus.REJECTED, FeedbackStatus.DUPLICATE},
        FeedbackStatus.PLANNED: {FeedbackStatus.IN_PROGRESS, FeedbackStatus.RESOLVED, FeedbackStatus.REJECTED, FeedbackStatus.DUPLICATE},
        FeedbackStatus.IN_PROGRESS: {FeedbackStatus.PLANNED, FeedbackStatus.RESOLVED, FeedbackStatus.REJECTED, FeedbackStatus.DUPLICATE},
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
