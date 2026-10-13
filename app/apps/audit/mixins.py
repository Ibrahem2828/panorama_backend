from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import models, transaction

from .services import AuditLogService

_MAX_AUDITED_TEXT_LENGTH = 200


def audit_snapshot(instance) -> dict[str, Any]:
    """Short, non-sensitive picture of a model row: scalar columns and foreign-key ids only.

    Free text (TextFields such as descriptions and messages), files and secrets are left out by design; their names
    still appear in ``changed_fields``. The audit sanitizer additionally redacts well-known sensitive keys.
    """

    snapshot: dict[str, Any] = {}
    for field in instance._meta.concrete_fields:
        if field.name in {"created_at", "updated_at", "deleted_at"} or isinstance(field, models.TextField):
            continue  # free text (descriptions, messages) never goes into the trail; its name is in changed_fields
        value = getattr(instance, field.attname)
        if value is None or isinstance(value, bool | int | float | Decimal | date | datetime | UUID):
            snapshot[field.name] = value
        elif isinstance(value, str):
            if len(value) <= _MAX_AUDITED_TEXT_LENGTH:
                snapshot[field.name] = value
        elif isinstance(value, list | dict) and len(str(value)) <= _MAX_AUDITED_TEXT_LENGTH:
            snapshot[field.name] = value
    return snapshot


class AuditedModelWriteMixin:
    """Audit create/update/delete of a ``StandardModelViewSet`` in the same transaction as the business change.

    A failed audit write raises (``critical=True``), which rolls the change back: a configuration change that cannot
    be recorded must not happen. Exactly one audit row is written per request. Set the three ``audit_*_action``
    attributes; override ``resolve_update_action`` when the action depends on what changed.
    """

    audit_create_action: str = ""
    audit_update_action: str = ""
    audit_delete_action: str = ""

    def audit_save_kwargs(self, *, creating: bool) -> dict[str, Any]:
        return {}

    def resolve_update_action(self, old: dict[str, Any], new: dict[str, Any]) -> str:
        return self.audit_update_action

    def resolve_delete_action(self, old: dict[str, Any]) -> str:
        return self.audit_delete_action

    def _audit(self, action: str, instance, *, old=None, new=None, changed: list[str] | None = None) -> None:
        new_value: dict[str, Any] = dict(new or {})
        if changed is not None:
            new_value["changed_fields"] = changed
        AuditLogService.log(
            actor=self.request.user,  # type: ignore[attr-defined]
            action=action,
            target=instance,
            old_value=old,
            new_value=new_value or None,
            request=self.request,  # type: ignore[attr-defined]
            critical=True,
        )

    def perform_create(self, serializer):
        with transaction.atomic():
            instance = serializer.save(**self.audit_save_kwargs(creating=True))
            self._audit(self.audit_create_action, instance, new=audit_snapshot(instance))

    def perform_update(self, serializer):
        with transaction.atomic():
            # Re-read the row: some serializers mutate ``serializer.instance`` during validation, which would
            # hide the change from a snapshot taken off the in-memory object.
            stored = type(serializer.instance)._default_manager.get(pk=serializer.instance.pk)
            old = audit_snapshot(stored)
            instance = serializer.save(**self.audit_save_kwargs(creating=False))
            new = audit_snapshot(instance)
            keys = set(old) | set(new)
            changed = sorted(key for key in keys if old.get(key) != new.get(key))
            self._audit(
                self.resolve_update_action(old, new),
                instance,
                old={key: old[key] for key in changed if key in old},
                new={key: new[key] for key in changed if key in new},
                changed=changed,
            )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()  # type: ignore[attr-defined]
        old = audit_snapshot(instance)
        with transaction.atomic():
            response = super().destroy(request, *args, **kwargs)  # type: ignore[misc]
            self._audit(self.resolve_delete_action(old), instance, old=old, new={"deleted": True})
        return response
