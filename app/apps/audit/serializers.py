from __future__ import annotations

from typing import Any

from django.apps import apps as django_apps
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import AuditLog, SystemActivity

_DISPLAY_ATTRS = ("full_name", "name", "name_ar", "title", "title_ar", "label", "code", "key", "slug", "email")
_MODEL_BY_NAME: dict[str, Any] | None = None


def _models_by_name() -> dict[str, Any]:
    """``target_type`` (a class name) -> model, only where the class name is unambiguous across apps."""

    global _MODEL_BY_NAME
    if _MODEL_BY_NAME is None:
        seen: dict[str, Any] = {}
        ambiguous: set[str] = set()
        for model in django_apps.get_models():
            name = model.__name__
            if name in seen:
                ambiguous.add(name)
            seen[name] = model
        _MODEL_BY_NAME = {k: v for k, v in seen.items() if k not in ambiguous}
    return _MODEL_BY_NAME


def resolve_target_labels(rows) -> dict[tuple[str, str], str]:
    """Best-effort ``"Model #id · display name"`` for a page of audit rows: one query per target type, no per-row queries."""

    wanted: dict[str, set[str]] = {}
    for row in rows:
        if row.target_type and row.target_id:
            wanted.setdefault(row.target_type, set()).add(row.target_id)
    labels: dict[tuple[str, str], str] = {}
    models_by_name = _models_by_name()
    for target_type, ids in wanted.items():
        for target_id in ids:
            labels[(target_type, target_id)] = f"{target_type} #{target_id}"
        model = models_by_name.get(target_type)
        if model is None:
            continue
        attr = next((a for a in _DISPLAY_ATTRS if any(f.name == a for f in model._meta.concrete_fields)), None)
        if attr is None:
            continue
        try:
            pks = [int(i) for i in ids if i.isdigit()]
            for pk, display in model._base_manager.filter(pk__in=pks).values_list("pk", attr):
                if display:
                    labels[(target_type, str(pk))] = f"{target_type} #{pk} · {str(display)[:120]}"
        except Exception:  # noqa: BLE001 - a label is a nicety; never fail the audit list over it
            continue
    return labels


class AuditLogListSerializer(serializers.ListSerializer):
    def to_representation(self, data):
        rows = list(data.all() if hasattr(data, "all") else data)
        self.context["target_labels"] = resolve_target_labels(rows)
        return super().to_representation(rows)


class AuditLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.full_name", read_only=True)
    actor_detail = serializers.SerializerMethodField()
    target_label = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        list_serializer_class = AuditLogListSerializer
        fields = [
            "id",
            "actor",
            "actor_name",
            "actor_detail",
            "action",
            "target_type",
            "target_id",
            "target_label",
            "old_value",
            "new_value",
            "ip_address",
            "user_agent",
            "created_at",
        ]
        read_only_fields = fields

    @extend_schema_field(
        {
            "type": "object",
            "nullable": True,
            "properties": {
                "id": {"type": "integer"},
                "full_name": {"type": "string"},
                "email": {"type": "string"},
                "role": {"type": "string"},
            },
        }
    )
    def get_actor_detail(self, obj: AuditLog):
        actor = obj.actor
        if actor is None:
            return None
        return {"id": actor.pk, "full_name": actor.full_name, "email": actor.email, "role": actor.role}

    def get_target_label(self, obj: AuditLog) -> str:
        if not obj.target_type:
            return ""
        labels = self.context.get("target_labels")
        if labels is None:  # single-object representation (retrieve)
            labels = resolve_target_labels([obj])
        return labels.get((obj.target_type, obj.target_id), f"{obj.target_type} #{obj.target_id}".strip())


class ActivityUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    full_name = serializers.CharField()
    email = serializers.CharField()
    role = serializers.CharField()
    job_title = serializers.CharField()


class SystemActivitySerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()

    class Meta:
        model = SystemActivity
        fields = [
            "id",
            "created_at",
            "user",
            "user_label",
            "kind",
            "action_label",
            "method",
            "route",
            "status_code",
            "object_type",
            "object_id",
            "ip",
            "user_agent",
            "duration_ms",
            "extra",
        ]
        read_only_fields = fields

    @extend_schema_field(ActivityUserSerializer(allow_null=True))
    def get_user(self, obj: SystemActivity):
        user = obj.user
        if user is None:
            return None
        return {
            "id": user.pk,
            "full_name": user.full_name,
            "email": user.email,
            "role": user.role,
            "job_title": user.job_title,
        }


class SystemActivitySummarySerializer(serializers.Serializer):
    total = serializers.IntegerField()
    errors = serializers.IntegerField()  # type: ignore[assignment]  # contract field name; shadows Serializer.errors
    failed_logins = serializers.IntegerField()
    active_users = serializers.IntegerField()
    by_kind = serializers.DictField(child=serializers.IntegerField())
    window = serializers.DictField(child=serializers.CharField(allow_null=True))


class SystemActivityUserRollupSerializer(serializers.Serializer):
    user = ActivityUserSerializer()
    last_activity_at = serializers.DateTimeField()
    requests_24h = serializers.IntegerField()
    requests_7d = serializers.IntegerField()
    failed_logins_24h = serializers.IntegerField()
    last_action_label = serializers.CharField(allow_blank=True)
    last_ip = serializers.CharField(allow_blank=True)
