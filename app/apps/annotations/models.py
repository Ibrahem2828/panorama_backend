"""A student's private marks on document pages: highlights, underlines, pen strokes and sticky text notes.

Marks are stored as page-relative coordinates (0..1), never as images, so they stay exact at any zoom and on any device.
A per-document ``revision`` counter lets a device ask "what changed since revision N" and merge without clobbering
another device; each mark also carries a ``version`` for item-level conflict detection.
"""

from __future__ import annotations

from django.db import models

from apps.common.models import BaseModel


class AnnotationTarget(models.TextChoices):
    LECTURE = "lecture", "Lecture"
    FILE = "file", "Library file"


class AnnotationKind(models.TextChoices):
    HIGHLIGHT = "highlight", "Highlight"
    UNDERLINE = "underline", "Underline"
    PEN = "pen", "Pen stroke"
    NOTE = "note", "Text note"


class AnnotationDocument(BaseModel):
    """One user's annotation set for one lecture/file, with the revision counter used for sync."""

    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="annotation_documents")
    target_type = models.CharField(max_length=16, choices=AnnotationTarget.choices)
    target_id = models.PositiveBigIntegerField()
    revision = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "target_type", "target_id"], name="annotation_document_unique")
        ]
        indexes = [models.Index(fields=["user", "updated_at"], name="annotation_document_recent_idx")]


class PageAnnotation(BaseModel):
    document = models.ForeignKey(AnnotationDocument, on_delete=models.CASCADE, related_name="items")
    #: Chosen by the device that drew it (UUID string); makes a retried upload idempotent.
    client_id = models.CharField(max_length=64)
    page_number = models.PositiveIntegerField()
    kind = models.CharField(max_length=16, choices=AnnotationKind.choices)
    color = models.CharField(max_length=16, blank=True)
    width = models.PositiveSmallIntegerField(default=0)
    #: highlight/underline: {"rects": [[x, y, w, h], ...]}; pen: {"points": [[x, y], ...]}; note: {"x", "y", "text"}.
    data = models.JSONField(default=dict)
    text = models.TextField(blank=True)  # searchable copy of a note's text
    version = models.PositiveIntegerField(default=1)
    #: The document revision at which this item last changed (tombstones included, so deletions sync too).
    revision = models.PositiveBigIntegerField(default=0, db_index=True)

    class Meta:
        ordering = ["page_number", "id"]
        constraints = [
            models.UniqueConstraint(fields=["document", "client_id"], name="annotation_client_id_unique"),
            models.CheckConstraint(condition=models.Q(page_number__gte=1), name="annotation_valid_page"),
        ]
        indexes = [
            models.Index(fields=["document", "page_number"], name="annotation_page_idx"),
            models.Index(fields=["document", "revision"], name="annotation_revision_idx"),
        ]
