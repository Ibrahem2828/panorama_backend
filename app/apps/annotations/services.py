from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import NotFound, ValidationError

from apps.files.models import FileResource
from apps.files.services import accessible_files_for_user, user_can_access_file
from apps.lectures.models import Lecture
from apps.lectures.services import accessible_lectures_for_user

from .models import AnnotationDocument, AnnotationKind, AnnotationTarget, PageAnnotation

MAX_ITEMS_PER_DOCUMENT = 5000
MAX_OPS_PER_SYNC = 200
MAX_DATA_BYTES = 64 * 1024
MAX_POINTS = 3000
MAX_RECTS = 200
MAX_NOTE_CHARS = 2000
COLORS = {"yellow", "green", "pink", "blue", "black", "red"}
WIDTHS = {1, 2}  # thin / thick (pen); highlights and underlines use 1


def resolve_target(user, target_type: str, target_id: int) -> tuple[str, int, int]:
    """Return (type, id, page_count) of a document the user may read, or raise 404."""

    if target_type == AnnotationTarget.LECTURE:
        lecture = get_object_or_404(accessible_lectures_for_user(user), pk=target_id)
        return target_type, lecture.pk, lecture.page_count
    if target_type == AnnotationTarget.FILE:
        resource = get_object_or_404(accessible_files_for_user(user), pk=target_id)
        if not user_can_access_file(user, resource):
            raise NotFound("File is unavailable.")
        return target_type, resource.pk, resource.pages_count or 0
    raise NotFound("Unknown document type.")


def _unit(value: Any) -> float:
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise ValueError("coordinate outside the page")
    return round(number, 5)


def clean_data(kind: str, data: Any) -> tuple[dict, str]:
    """Validate and normalise a mark's geometry. Returns (data, searchable text)."""

    if not isinstance(data, dict) or len(json.dumps(data)) > MAX_DATA_BYTES:
        raise ValidationError({"data": "Invalid annotation data."})
    try:
        if kind in (AnnotationKind.HIGHLIGHT, AnnotationKind.UNDERLINE):
            rects = data.get("rects")
            if not isinstance(rects, list) or not 1 <= len(rects) <= MAX_RECTS:
                raise ValueError("rects")
            cleaned = []
            for rect in rects:
                x, y, w, h = (_unit(part) for part in rect)
                cleaned.append([x, y, min(w, 1 - x), min(h, 1 - y)])
            return {"rects": cleaned}, ""
        if kind == AnnotationKind.PEN:
            points = data.get("points")
            if not isinstance(points, list) or not 2 <= len(points) <= MAX_POINTS:
                raise ValueError("points")
            return {"points": [[_unit(x), _unit(y)] for x, y in points]}, ""
        if kind == AnnotationKind.NOTE:
            text = str(data.get("text", "")).strip()
            if not text or len(text) > MAX_NOTE_CHARS:
                raise ValueError("text")
            return {"x": _unit(data["x"]), "y": _unit(data["y"]), "text": text}, text
    except (TypeError, ValueError, KeyError) as exc:
        raise ValidationError({"data": "Invalid annotation data."}) from exc
    raise ValidationError({"kind": "Unknown annotation kind."})


def serialize(item: PageAnnotation) -> dict:
    return {
        "client_id": item.client_id,
        "page_number": item.page_number,
        "kind": item.kind,
        "color": item.color,
        "width": item.width,
        "data": item.data,
        "version": item.version,
        "revision": item.revision,
        "deleted": item.is_deleted,
        "updated_at": item.updated_at,
    }


@dataclass
class SyncResult:
    revision: int
    applied: int = 0
    conflicts: list[dict] = field(default_factory=list)
    changes: list[dict] = field(default_factory=list)


class AnnotationService:
    @staticmethod
    def changes_since(user, target_type: str, target_id: int, since: int) -> tuple[int, list[dict]]:
        document = AnnotationDocument.objects.filter(user=user, target_type=target_type, target_id=target_id).first()
        if document is None:
            return 0, []
        items = document.items.filter(revision__gt=since).order_by("revision", "id")
        return document.revision, [serialize(item) for item in items]

    @classmethod
    @transaction.atomic
    def sync(
        cls,
        user,
        target_type: str,
        target_id: int,
        page_count: int,
        *,
        base_revision: int,
        upserts: list[dict],
        deletes: list[str],
    ) -> SyncResult:
        """Apply a device's offline changes, then return everything that changed since its ``base_revision``.

        Item rule: an upsert that names the server ``version`` it was based on wins only if that version is still
        current; otherwise it is reported as a conflict carrying the server copy and nothing is overwritten.
        Deleting is idempotent. New marks are idempotent on ``client_id``.
        """

        if len(upserts) + len(deletes) > MAX_OPS_PER_SYNC:
            raise ValidationError({"detail": f"At most {MAX_OPS_PER_SYNC} changes per sync."})
        document, _created = AnnotationDocument.objects.select_for_update().get_or_create(
            user=user, target_type=target_type, target_id=target_id
        )
        result = SyncResult(revision=document.revision)
        touched = False
        existing = {item.client_id: item for item in document.items.select_for_update()}
        live_count = sum(1 for item in existing.values() if not item.is_deleted)

        def bump() -> int:
            nonlocal touched
            touched = True
            document.revision += 1
            return document.revision

        for op in upserts:
            client_id = str(op.get("client_id", ""))[:64]
            kind = op.get("kind")
            page = op.get("page_number")
            if not client_id or kind not in AnnotationKind.values:
                raise ValidationError({"upserts": "Each change needs a client_id and a valid kind."})
            if not isinstance(page, int) or not 1 <= page <= max(page_count, 1):
                raise ValidationError({"upserts": "Page number is outside the document."})
            color = str(op.get("color", ""))
            if color and color not in COLORS:
                raise ValidationError({"upserts": "Unknown color."})
            width = int(op.get("width") or 0)
            if width and width not in WIDTHS:
                raise ValidationError({"upserts": "Unknown width."})
            data, text = clean_data(kind, op.get("data"))
            current = existing.get(client_id)
            if current is None:
                if live_count >= MAX_ITEMS_PER_DOCUMENT:
                    raise ValidationError({"detail": "Too many annotations in this document."})
                created = PageAnnotation.objects.create(
                    document=document,
                    client_id=client_id,
                    page_number=page,
                    kind=kind,
                    color=color,
                    width=width,
                    data=data,
                    text=text,
                    revision=bump(),
                )
                existing[client_id] = created
                live_count += 1
                result.applied += 1
                continue
            based_on = op.get("version")
            if based_on is not None and int(based_on) != current.version:
                result.conflicts.append(serialize(current))
                continue
            if current.is_deleted:
                live_count += 1
            current.page_number = page
            current.kind = kind
            current.color = color
            current.width = width
            current.data = data
            current.text = text
            current.is_deleted = False
            current.deleted_at = None
            current.version += 1
            current.revision = bump()
            current.save()
            result.applied += 1

        for client_id in deletes:
            current = existing.get(str(client_id))
            if current is None or current.is_deleted:
                continue
            current.is_deleted = True
            current.version += 1
            current.revision = bump()
            current.save(update_fields=["is_deleted", "version", "revision", "updated_at"])
            result.applied += 1

        if touched:
            document.save(update_fields=["revision", "updated_at"])
        result.revision = document.revision
        result.changes = [
            serialize(item) for item in document.items.filter(revision__gt=base_revision).order_by("revision", "id")
        ]
        return result


def search_my_notes(user, query: str, limit: int = 30) -> list[dict]:
    """Text notes + bookmarks across lectures and files, newest first, optionally filtered by ``query``."""

    from django.db.models import Q

    needle = " ".join((query or "").split())
    rows: list[dict] = []

    from apps.lectures.models import LectureNote

    notes = LectureNote.objects.filter(student=user, is_deleted=False, archived_at__isnull=True).select_related(
        "lecture"
    )
    if needle:
        notes = notes.filter(Q(content__icontains=needle) | Q(selected_text__icontains=needle))
    for note in notes.order_by("-updated_at")[:limit]:
        rows.append(
            {
                "source": "lecture_note",
                "target_type": AnnotationTarget.LECTURE,
                "target_id": note.lecture_id,
                "title": note.lecture.title,
                "page_number": note.page_number,
                "text": note.content,
                "is_bookmark": note.is_bookmark,
                "updated_at": note.updated_at,
            }
        )

    marks = PageAnnotation.objects.filter(
        document__user=user, document__is_deleted=False, kind=AnnotationKind.NOTE, is_deleted=False
    ).select_related("document")
    if needle:
        marks = marks.filter(text__icontains=needle)
    recent = list(marks.order_by("-updated_at")[:limit])
    titles: dict[tuple[str, int], str] = {}
    lecture_ids = {m.document.target_id for m in recent if m.document.target_type == AnnotationTarget.LECTURE}
    file_ids = {m.document.target_id for m in recent if m.document.target_type == AnnotationTarget.FILE}
    for pk, title in Lecture.objects.filter(pk__in=lecture_ids).values_list("pk", "title"):
        titles[(AnnotationTarget.LECTURE, pk)] = title
    for pk, title in FileResource.objects.filter(pk__in=file_ids).values_list("pk", "title"):
        titles[(AnnotationTarget.FILE, pk)] = title
    for mark in recent:
        key = (mark.document.target_type, mark.document.target_id)
        rows.append(
            {
                "source": "annotation",
                "target_type": mark.document.target_type,
                "target_id": mark.document.target_id,
                "title": titles.get(key, ""),
                "page_number": mark.page_number,
                "text": mark.text,
                "is_bookmark": False,
                "updated_at": mark.updated_at,
            }
        )
    rows.sort(key=lambda row: row["updated_at"], reverse=True)
    return rows[:limit]
