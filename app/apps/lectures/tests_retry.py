"""Dashboard lecture retry, re-upload after delete, and the publish/unpublish flow through the API."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.files.base import ContentFile
from django.utils import timezone
from rest_framework import status

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.audit.models import AuditAction, AuditLog
from apps.lectures import tests as base
from apps.lectures.models import Lecture, LectureProcessingStatus
from apps.product.models import FeatureFlag

# Reuse the fixtures of the main lecture tests (assignment keeps them registered without shadowing arguments).
academic_structure = base.academic_structure
api_client = base.api_client
manager = base.manager
ready_lecture = base.ready_lecture
student = base.student
make_pdf = base.make_pdf


def _failed(lecture: Lecture, **extra) -> Lecture:
    Lecture.objects.filter(pk=lecture.pk).update(
        status=LectureProcessingStatus.FAILED, failure_code="PROCESSING_FAILED", failure_message="boom", **extra
    )
    lecture.refresh_from_db()
    return lecture


def _retry(client, lecture, **headers):
    with patch("apps.lectures.views.transaction.on_commit", side_effect=lambda cb: cb()):
        return client.post(f"/api/v1/dashboard/lectures/{lecture.id}/retry/", **headers)


@pytest.mark.django_db
def test_retry_requeues_failed_lecture_and_audits(api_client, manager, ready_lecture):
    _failed(ready_lecture)
    api_client.force_authenticate(manager)
    with patch("apps.lectures.views.process_lecture_document.delay") as delay:
        response = _retry(api_client, ready_lecture)
    assert response.status_code == status.HTTP_202_ACCEPTED
    assert response.data["data"]["status"] == LectureProcessingStatus.QUEUED
    assert response.data["data"]["failure_code"] == ""
    delay.assert_called_once_with(ready_lecture.id)
    ready_lecture.refresh_from_db()
    assert ready_lecture.status == LectureProcessingStatus.QUEUED
    entry = AuditLog.objects.get(action=AuditAction.LECTURE_PROCESSING_RETRIED)
    assert entry.target_id == str(ready_lecture.id)
    assert entry.old_value["status"] == LectureProcessingStatus.FAILED


@pytest.mark.django_db
@pytest.mark.parametrize(
    "state",
    [LectureProcessingStatus.READY, LectureProcessingStatus.QUARANTINED, LectureProcessingStatus.QUEUED],
)
def test_retry_conflict_when_not_retryable(api_client, manager, ready_lecture, state):
    Lecture.objects.filter(pk=ready_lecture.pk).update(status=state)
    api_client.force_authenticate(manager)
    with patch("apps.lectures.views.process_lecture_document.delay") as delay:
        response = _retry(api_client, ready_lecture)
    assert response.status_code == status.HTTP_409_CONFLICT
    delay.assert_not_called()
    assert not AuditLog.objects.filter(action=AuditAction.LECTURE_PROCESSING_RETRIED).exists()


@pytest.mark.django_db
def test_retry_allows_stalled_in_flight_lecture_only_after_horizon(api_client, manager, ready_lecture):
    old = timezone.now() - timedelta(minutes=45)
    Lecture.objects.filter(pk=ready_lecture.pk).update(status=LectureProcessingStatus.RENDERING, updated_at=old)
    api_client.force_authenticate(manager)
    with patch("apps.lectures.views.process_lecture_document.delay") as delay:
        assert _retry(api_client, ready_lecture).status_code == status.HTTP_202_ACCEPTED
    delay.assert_called_once()

    Lecture.objects.filter(pk=ready_lecture.pk).update(
        status=LectureProcessingStatus.RENDERING, updated_at=timezone.now()
    )
    assert _retry(api_client, ready_lecture).status_code == status.HTTP_409_CONFLICT


@pytest.mark.django_db
def test_retry_is_idempotent_with_key(api_client, manager, ready_lecture):
    _failed(ready_lecture)
    api_client.force_authenticate(manager)
    with patch("apps.lectures.views.process_lecture_document.delay") as delay:
        first = _retry(api_client, ready_lecture, HTTP_IDEMPOTENCY_KEY="retry-key-0001")
        second = _retry(api_client, ready_lecture, HTTP_IDEMPOTENCY_KEY="retry-key-0001")
    assert first.status_code == second.status_code == status.HTTP_202_ACCEPTED
    delay.assert_called_once()
    assert AuditLog.objects.filter(action=AuditAction.LECTURE_PROCESSING_RETRIED).count() == 1


@pytest.mark.django_db
def test_retry_permissions_and_missing(api_client, manager, ready_lecture, student):
    _failed(ready_lecture)
    api_client.force_authenticate(student)
    assert _retry(api_client, ready_lecture).status_code == status.HTTP_403_FORBIDDEN
    support = User.objects.create_user(
        full_name="Support",
        email="retry-support@example.com",
        phone_number="+963955000099",
        password="StrongPass123!",
        role=UserRole.SUPPORT_STAFF,
    )
    api_client.force_authenticate(support)
    assert _retry(api_client, ready_lecture).status_code == status.HTTP_403_FORBIDDEN
    api_client.force_authenticate(manager)
    assert api_client.post("/api/v1/dashboard/lectures/999999/retry/").status_code == status.HTTP_404_NOT_FOUND
    ready_lecture.refresh_from_db()
    assert ready_lecture.status == LectureProcessingStatus.FAILED
    api_client.force_authenticate(None)
    assert api_client.post(f"/api/v1/dashboard/lectures/{ready_lecture.id}/retry/").status_code == 401


@pytest.mark.django_db
def test_retry_respects_processing_flag(api_client, manager, ready_lecture):
    _failed(ready_lecture)
    FeatureFlag.objects.create(key="lecture_processing_enabled", enabled=False)
    api_client.force_authenticate(manager)
    assert _retry(api_client, ready_lecture).status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    ready_lecture.refresh_from_db()
    assert ready_lecture.status == LectureProcessingStatus.FAILED


@pytest.mark.django_db
def test_deleted_lecture_document_can_be_uploaded_again(api_client, manager, academic_structure):
    api_client.force_authenticate(manager)
    payload = {"subject": academic_structure["subject"].id, "title": "Week 1"}
    with patch("apps.lectures.views.process_lecture_document.delay"):
        with patch("apps.lectures.views.transaction.on_commit", side_effect=lambda cb: cb()):
            first = api_client.post(
                "/api/v1/dashboard/lectures/",
                {**payload, "original_file": ContentFile(make_pdf(), name="w1.pdf")},
                format="multipart",
            )
            duplicate = api_client.post(
                "/api/v1/dashboard/lectures/",
                {**payload, "original_file": ContentFile(make_pdf(), name="w1.pdf")},
                format="multipart",
            )
            assert first.status_code == status.HTTP_201_CREATED
            assert duplicate.status_code == status.HTTP_400_BAD_REQUEST

            lecture_id = first.data["data"]["id"]
            assert api_client.delete(f"/api/v1/dashboard/lectures/{lecture_id}/").status_code == status.HTTP_200_OK
            again = api_client.post(
                "/api/v1/dashboard/lectures/",
                {**payload, "original_file": ContentFile(make_pdf(), name="w1.pdf")},
                format="multipart",
            )
    assert again.status_code == status.HTTP_201_CREATED
    hashes = Lecture.objects.filter(subject=academic_structure["subject"]).values_list("original_sha256", "is_deleted")
    assert sorted(hashes, key=lambda row: row[1]) == [(first.data["data"]["original_sha256"],) * 1 + (False,)] + [
        (first.data["data"]["original_sha256"], True)
    ]
    assert AuditLog.objects.filter(action=AuditAction.LECTURE_DELETED).count() == 1


@pytest.mark.django_db
def test_publish_unpublish_flow_end_to_end(api_client, manager, student, academic_structure):
    api_client.force_authenticate(manager)
    with patch("apps.lectures.views.process_lecture_document.delay"):
        with patch("apps.lectures.views.transaction.on_commit", side_effect=lambda cb: cb()):
            created = api_client.post(
                "/api/v1/dashboard/lectures/",
                {
                    "subject": academic_structure["subject"].id,
                    "title": "Flow",
                    "original_file": ContentFile(make_pdf(), name="flow.pdf"),
                },
                format="multipart",
            )
    assert created.status_code == status.HTTP_201_CREATED
    lecture = Lecture.objects.get(pk=created.data["data"]["id"])
    assert lecture.status == LectureProcessingStatus.QUEUED and not lecture.is_published

    # Simulate the worker finishing, then publish through the API.
    Lecture.objects.filter(pk=lecture.pk).update(
        status=LectureProcessingStatus.READY, viewer_pdf=ContentFile(make_pdf(), name="v.pdf").name, page_count=1
    )
    api_client.force_authenticate(student)
    assert api_client.get(f"/api/v1/lectures/{lecture.pk}/").status_code == status.HTTP_404_NOT_FOUND

    api_client.force_authenticate(manager)
    published = api_client.patch(f"/api/v1/dashboard/lectures/{lecture.pk}/", {"is_published": True}, format="json")
    assert published.status_code == status.HTTP_200_OK
    assert published.data["data"]["published_at"] is not None
    api_client.force_authenticate(student)
    assert api_client.get(f"/api/v1/lectures/{lecture.pk}/").status_code == status.HTTP_200_OK

    api_client.force_authenticate(manager)
    api_client.patch(f"/api/v1/dashboard/lectures/{lecture.pk}/", {"is_published": False}, format="json")
    api_client.force_authenticate(student)
    assert api_client.get(f"/api/v1/lectures/{lecture.pk}/").status_code == status.HTTP_404_NOT_FOUND
    assert AuditLog.objects.filter(action=AuditAction.LECTURE_UPDATED).count() == 2

    api_client.force_authenticate(manager)
    filtered = api_client.get("/api/v1/dashboard/lectures/?status=ready&is_published=false")
    assert [row["id"] for row in filtered.data["data"]["results"]] == [lecture.pk]
    other = api_client.get("/api/v1/dashboard/lectures/?status=failed")
    assert other.data["data"]["results"] == []


@pytest.mark.django_db
def test_constraint_is_unique_only_among_live_rows(ready_lecture):
    clone = Lecture(
        subject=ready_lecture.subject,
        title="dup",
        original_file=ready_lecture.original_file.name,
        original_filename="o.pdf",
        original_mime_type="application/pdf",
        original_size=1,
        original_sha256=ready_lecture.original_sha256,
        uploaded_by=ready_lecture.uploaded_by,
    )
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError), transaction.atomic():
        clone.save()
    Lecture.objects.filter(pk=ready_lecture.pk).update(is_deleted=True)
    clone.save()
    assert clone.pk


@pytest.mark.django_db(transaction=True)
def test_migration_is_reversible_and_guards_the_reverse_path(ready_lecture):
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    executor.migrate([("lectures", "0001_initial")])  # reversible when the rows are unique
    executor = MigrationExecutor(connection)
    executor.migrate([("lectures", "0002_lecture_unique_hash_live_only")])

    Lecture.objects.filter(pk=ready_lecture.pk).update(is_deleted=True)
    Lecture.objects.create(
        subject=ready_lecture.subject,
        title="re-upload",
        original_file="x.pdf",
        original_filename="x.pdf",
        original_mime_type="application/pdf",
        original_size=1,
        original_sha256=ready_lecture.original_sha256,
        uploaded_by=ready_lecture.uploaded_by,
    )
    executor = MigrationExecutor(connection)
    with pytest.raises(RuntimeError, match="no longer unique"):
        executor.migrate([("lectures", "0001_initial")])
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())
