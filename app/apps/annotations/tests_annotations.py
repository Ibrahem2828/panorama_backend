"""Private page annotations: validation, idempotent sync, conflicts, tombstones, isolation, my-notes search."""

from __future__ import annotations

import io

import pytest
from django.core.files.base import ContentFile
from PIL import Image
from rest_framework.test import APIClient

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.files.models import FileProcessingStatus, FileResource
from apps.lectures.models import Lecture, LectureNote
from apps.universities.models import University


def _client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10), "white").save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def world(db):
    university = University.objects.create(name="Notes University", code="NU")
    admin = User.objects.create_user(
        full_name="Admin",
        email="a@n.test",
        phone_number="+963993000001",
        password="StrongPass123!",
        role=UserRole.ADMIN,
    )
    users = []
    for number in (2, 3):
        user = User.objects.create_user(
            full_name=f"Student {number}",
            email=f"s{number}@n.test",
            phone_number=f"+96399300000{number}",
            password="StrongPass123!",
            role=UserRole.STUDENT,
        )
        StudentProfile.objects.create(
            user=user, university=university, verification_status=StudentVerificationStatus.APPROVED
        )
        users.append(user)
    resource = FileResource.objects.create(
        title="Anatomy",
        file=ContentFile(_png(), name="a.png"),
        uploaded_by=admin,
        university=university,
        visibility="verified_students_only",
        processing_status=FileProcessingStatus.READY,
        pages_count=5,
    )
    return {"admin": admin, "a": users[0], "b": users[1], "file": resource}


def _sync(client, resource, upserts=(), deletes=(), base=0):
    return client.post(
        f"/api/v1/annotations/files/{resource.pk}/sync/",
        {"base_revision": base, "upserts": list(upserts), "deletes": list(deletes)},
        format="json",
    )


def _highlight(client_id="h1", page=1, **extra):
    return {
        "client_id": client_id,
        "page_number": page,
        "kind": "highlight",
        "color": "yellow",
        "width": 1,
        "data": {"rects": [[0.1, 0.2, 0.5, 0.05]]},
        **extra,
    }


def test_create_sync_and_incremental_fetch(world):
    client = _client(world["a"])
    first = _sync(
        client,
        world["file"],
        [
            _highlight(),
            {**_highlight("p1", 2), "kind": "pen", "color": "black", "data": {"points": [[0.1, 0.1], [0.2, 0.3]]}},
        ],
    )
    assert first.status_code == 200, first.data
    data = first.data["data"]
    assert data["revision"] == 2 and data["applied"] == 2 and data["conflicts"] == []
    assert {item["client_id"] for item in data["changes"]} == {"h1", "p1"}

    nothing = client.get(f"/api/v1/annotations/files/{world['file'].pk}/?since=2")
    assert nothing.data["data"]["items"] == []
    some = client.get(f"/api/v1/annotations/files/{world['file'].pk}/?since=1")
    assert [item["client_id"] for item in some.data["data"]["items"]] == ["p1"]


def test_upload_is_idempotent_on_client_id(world):
    client = _client(world["a"])
    _sync(client, world["file"], [_highlight()])
    again = _sync(client, world["file"], [_highlight(version=1)])
    assert again.data["data"]["conflicts"] == []
    items = client.get(f"/api/v1/annotations/files/{world['file'].pk}/").data["data"]["items"]
    assert len(items) == 1 and items[0]["version"] == 2


def test_stale_version_is_a_conflict_and_changes_nothing(world):
    client = _client(world["a"])
    _sync(client, world["file"], [_highlight()])
    _sync(client, world["file"], [_highlight(color="green", version=1)])  # device 1 moves it to version 2
    stale = _sync(client, world["file"], [_highlight(color="pink", version=1)])  # device 2 still on version 1
    result = stale.data["data"]
    assert result["applied"] == 0 and len(result["conflicts"]) == 1
    assert result["conflicts"][0]["color"] == "green" and result["conflicts"][0]["version"] == 2


def test_delete_is_a_tombstone_that_syncs_and_is_idempotent(world):
    client = _client(world["a"])
    _sync(client, world["file"], [_highlight()])
    deleted = _sync(client, world["file"], deletes=["h1"], base=1)
    assert [(item["client_id"], item["deleted"]) for item in deleted.data["data"]["changes"]] == [("h1", True)]
    again = _sync(client, world["file"], deletes=["h1", "never-existed"], base=2)
    assert again.data["data"]["applied"] == 0
    # Drawing the same id again brings it back.
    back = _sync(client, world["file"], [_highlight()], base=2)
    assert back.data["data"]["changes"][0]["deleted"] is False


def test_annotations_are_private_per_user(world):
    _sync(_client(world["a"]), world["file"], [_highlight()])
    other = _client(world["b"]).get(f"/api/v1/annotations/files/{world['file'].pk}/")
    assert other.data["data"]["items"] == []


def test_validation_rejects_bad_geometry_pages_and_colors(world):
    client = _client(world["a"])
    assert _sync(client, world["file"], [_highlight(page=9)]).status_code == 400  # file has 5 pages
    assert _sync(client, world["file"], [_highlight(data={"rects": [[2, 0, 1, 1]]})]).status_code == 400
    assert _sync(client, world["file"], [_highlight(color="magenta")]).status_code == 400
    assert (
        _sync(client, world["file"], [{**_highlight(), "kind": "pen", "data": {"points": [[0.1, 0.1]]}}]).status_code
        == 400
    )
    assert (
        _sync(
            client, world["file"], [{**_highlight(), "kind": "note", "data": {"x": 0.1, "y": 0.1, "text": "  "}}]
        ).status_code
        == 400
    )
    assert _sync(client, world["file"], [{**_highlight(), "kind": "scribble"}]).status_code == 400
    too_many = [_highlight(f"h{n}") for n in range(201)]
    assert _sync(client, world["file"], too_many).status_code == 400


def test_documents_the_user_cannot_read_are_404(world):
    resource = world["file"]
    resource.visibility = "admin_only"
    resource.save()
    assert _sync(_client(world["a"]), resource, [_highlight()]).status_code == 404
    assert _client(world["a"]).get("/api/v1/annotations/books/1/").status_code in (400, 404)
    assert _client(world["a"]).get("/api/v1/annotations/files/999999/").status_code == 404


def test_my_notes_search_spans_lecture_notes_and_text_marks(world):
    client = _client(world["a"])
    _sync(
        client,
        world["file"],
        [
            {
                **_highlight("n1", 3),
                "kind": "note",
                "color": "",
                "data": {"x": 0.2, "y": 0.3, "text": "remember the mandible"},
            }
        ],
    )
    lecture = Lecture.objects.create(
        title="Skull lecture",
        original_file=ContentFile(b"x", name="l.pdf"),
        original_filename="l.pdf",
        original_mime_type="application/pdf",
        original_size=1,
        original_sha256="a" * 64,
        uploaded_by=world["admin"],
        page_count=4,
    )
    LectureNote.objects.create(student=world["a"], lecture=lecture, page_number=2, content="mandible exam question")
    LectureNote.objects.create(student=world["b"], lecture=lecture, page_number=2, content="mandible of student b")

    found = client.get("/api/v1/my-notes/?q=mandible").data["data"]
    assert {row["source"] for row in found} == {"lecture_note", "annotation"}
    assert all("student b" not in row["text"] for row in found)
    assert {row["title"] for row in found} == {"Skull lecture", "Anatomy"}
    assert client.get("/api/v1/my-notes/?q=zzzz").data["data"] == []
    assert len(client.get("/api/v1/my-notes/").data["data"]) == 2
