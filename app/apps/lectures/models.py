from __future__ import annotations

import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel


class LectureProcessingStatus(models.TextChoices):
    UPLOADED = "uploaded", "Uploaded"
    QUEUED = "queued", "Queued"
    SCANNING = "scanning", "Scanning"
    CONVERTING = "converting", "Converting"
    EXTRACTING = "extracting", "Extracting"
    RENDERING = "rendering", "Rendering"
    READY = "ready", "Ready"
    FAILED = "failed", "Failed"
    QUARANTINED = "quarantined", "Quarantined"


class Lecture(BaseModel):
    subject = models.ForeignKey("universities.Subject", on_delete=models.PROTECT, related_name="lectures")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    original_file = models.FileField(upload_to="lectures/originals/")
    original_filename = models.CharField(max_length=255)
    original_mime_type = models.CharField(max_length=128)
    original_size = models.PositiveBigIntegerField()
    original_sha256 = models.CharField(max_length=64, db_index=True)
    viewer_pdf = models.FileField(upload_to="lectures/viewer/", blank=True)
    status = models.CharField(
        max_length=16,
        choices=LectureProcessingStatus.choices,
        default=LectureProcessingStatus.UPLOADED,
    )
    page_count = models.PositiveIntegerField(default=0)
    processing_task_id = models.CharField(max_length=255, blank=True)
    failure_code = models.CharField(max_length=64, blank=True)
    failure_message = models.CharField(max_length=255, blank=True)
    is_published = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True)
    uploaded_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="uploaded_lectures")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["subject", "status", "is_published"], name="lectures_subject_status_idx"),
            models.Index(fields=["is_published", "created_at"], name="lectures_published_created_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["subject", "original_sha256"],
                condition=~models.Q(original_sha256=""),
                name="lectures_subject_original_hash_uniq",
            )
        ]

    @property
    def is_ready_for_students(self) -> bool:
        return (
            self.status == LectureProcessingStatus.READY
            and self.is_published
            and not self.is_deleted
            and bool(self.viewer_pdf)
        )


class LecturePage(BaseModel):
    lecture = models.ForeignKey(Lecture, on_delete=models.CASCADE, related_name="pages")
    page_number = models.PositiveIntegerField()
    rendered_file = models.FileField(upload_to="lectures/pages/")
    thumbnail = models.FileField(upload_to="lectures/thumbnails/", blank=True)
    text_content = models.TextField(blank=True)
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["page_number"]
        constraints = [models.UniqueConstraint(fields=["lecture", "page_number"], name="lectures_page_unique")]
        indexes = [models.Index(fields=["lecture", "page_number"], name="lectures_page_lookup_idx")]


class LectureViewerSession(BaseModel):
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    lecture = models.ForeignKey(Lecture, on_delete=models.CASCADE, related_name="viewer_sessions")
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="lecture_viewer_sessions")
    expires_at = models.DateTimeField()
    max_page_requests = models.PositiveIntegerField()
    page_request_count = models.PositiveIntegerField(default=0)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["token", "expires_at"], name="lectures_session_token_idx")]

    @property
    def is_valid(self) -> bool:
        return (
            self.revoked_at is None
            and self.expires_at > timezone.now()
            and self.page_request_count < self.max_page_requests
            and self.lecture.is_ready_for_students
        )

    @classmethod
    def issue(cls, lecture: Lecture, user):
        return cls.objects.create(
            lecture=lecture,
            user=user,
            expires_at=timezone.now() + timedelta(seconds=getattr(settings, "LECTURE_VIEWER_SESSION_TTL_SECONDS", 900)),
            max_page_requests=getattr(settings, "LECTURE_VIEWER_SESSION_MAX_PAGE_REQUESTS", 1200),
        )


class LectureNote(BaseModel):
    student = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="lecture_notes")
    lecture = models.ForeignKey(Lecture, on_delete=models.CASCADE, related_name="notes")
    page_number = models.PositiveIntegerField(null=True, blank=True)
    selected_text = models.TextField(blank=True)
    anchor_data = models.JSONField(default=dict, blank=True)
    content = models.TextField()
    color = models.CharField(max_length=32, blank=True)
    note_type = models.CharField(max_length=32, default="note")
    is_bookmark = models.BooleanField(default=False)
    is_favorite = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)
    idempotency_key_hash = models.CharField(max_length=64, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["student", "lecture", "page_number"], name="lectures_note_student_page_idx"),
            models.Index(fields=["lecture", "updated_at"], name="lectures_note_updated_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(page_number__isnull=True) | models.Q(page_number__gte=1),
                name="lectures_note_valid_page_number",
            ),
            models.UniqueConstraint(
                fields=["student", "lecture", "idempotency_key_hash"],
                condition=~models.Q(idempotency_key_hash=""),
                name="lectures_note_idempotency_uniq",
            ),
        ]


class LectureViewEvent(BaseModel):
    lecture = models.ForeignKey(Lecture, on_delete=models.CASCADE, related_name="view_events")
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="lecture_view_events")
    session = models.ForeignKey(LectureViewerSession, on_delete=models.SET_NULL, null=True, blank=True)
    page_number = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["lecture", "created_at"], name="lectures_view_event_idx")]
