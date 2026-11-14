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
    """A lecture is EITHER linked to a subject (then only approved members of the subject's study group may read it) OR general:
    no subject, visible to every approved student, narrowed to one faculty when ``faculty`` is set."""

    subject = models.ForeignKey(
        "universities.Subject", on_delete=models.PROTECT, related_name="lectures", null=True, blank=True
    )
    faculty = models.ForeignKey(
        "universities.Faculty", on_delete=models.PROTECT, related_name="general_lectures", null=True, blank=True
    )
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
                # Soft-deleted lectures keep their row (and hash) but must not block re-uploading the same document.
                condition=~models.Q(original_sha256="") & models.Q(is_deleted=False),
                name="lectures_subject_original_hash_uniq",
            ),
            models.UniqueConstraint(
                fields=["original_sha256"],
                condition=~models.Q(original_sha256="") & models.Q(is_deleted=False) & models.Q(subject__isnull=True),
                name="lectures_general_original_hash_uniq",
            ),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.subject_id and self.faculty_id:
            raise ValidationError("A lecture linked to a subject takes its faculty from the subject.")

    @property
    def is_general(self) -> bool:
        return self.subject_id is None

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


class LectureOfflineBlob(BaseModel):
    """One encrypted, cacheable offline package of a lecture's viewable PDF (one per lecture *version*).

    The AES key is wrapped with the repo's Fernet field-encryption key; the ciphertext lives in private storage and is
    only ever served to holders of a valid ``LectureOfflineLicense``. A new version is created when the viewable
    document is replaced; retiring a blob revokes its licenses.
    """

    lecture = models.ForeignKey(Lecture, on_delete=models.CASCADE, related_name="offline_blobs")
    version = models.PositiveIntegerField()
    key_id = models.UUIDField(default=uuid.uuid4, editable=False)
    wrapped_key = models.TextField()
    package = models.FileField(upload_to="lectures/offline/")
    source_name = models.CharField(max_length=512, help_text="viewer_pdf storage name this blob was built from.")
    plaintext_size = models.PositiveBigIntegerField()
    package_size = models.PositiveBigIntegerField()
    package_sha256 = models.CharField(max_length=64)
    chunk_size = models.PositiveIntegerField()
    retired_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["lecture_id", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["lecture", "version"], name="lectures_offline_blob_version_uniq"),
            models.UniqueConstraint(
                fields=["lecture"],
                condition=models.Q(retired_at__isnull=True),
                name="lectures_offline_blob_one_live_uniq",
            ),
        ]


class LectureOfflineLicense(BaseModel):
    """Right of one (user, device) to keep one lecture version offline until ``expires_at``.

    Validity is *derived*: a license only works while ``user_can_view_lecture`` still holds, i.e. an approved
    membership of the lecture's group. Nothing payment-specific is consulted here.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="lecture_offline_licenses")
    lecture = models.ForeignKey(Lecture, on_delete=models.CASCADE, related_name="offline_licenses")
    blob = models.ForeignKey(LectureOfflineBlob, on_delete=models.PROTECT, related_name="licenses")
    #: SHA-256 hex of the ``X-Panorama-Device-Id`` header; empty when the client sent none.
    device_hash = models.CharField(max_length=64, blank=True, db_index=True)
    issued_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    last_renewed_at = models.DateTimeField(null=True, blank=True)
    renew_count = models.PositiveIntegerField(default=0)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoke_reason = models.CharField(max_length=64, blank=True)
    revoked_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-issued_at", "-id"]
        indexes = [
            models.Index(fields=["user", "revoked_at", "expires_at"], name="lectures_offlic_user_idx"),
            models.Index(fields=["lecture", "revoked_at"], name="lectures_offlic_lecture_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "lecture", "device_hash"],
                condition=models.Q(revoked_at__isnull=True),
                name="lectures_offlic_one_active_uniq",
            )
        ]
