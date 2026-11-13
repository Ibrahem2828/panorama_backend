"""Chat media: voice notes, uploaded audio, images/thumbnails and protected, range-aware delivery."""

from __future__ import annotations

import io
import json
import wave
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.chat.delivery import parse_range
from apps.chat.media import sniff_audio_family
from apps.chat.models import Message, MessageAttachmentAccessTicket, MessageReport, MessageType
from apps.chat.tests_handshake_tickets import _build_chat_fixture
from apps.groups.models import Group, GroupMembership, GroupMembershipStatus
from apps.universities.models import University

pytestmark = pytest.mark.django_db


def _client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


def make_wav(seconds: float, rate: int = 8000, extra: int = 0) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(1)
        handle.setframerate(rate)
        handle.writeframes(b"\x80" * int(seconds * rate))
    return buffer.getvalue() + b"\x00" * extra


def make_mp3(frames: int) -> bytes:
    # MPEG-1 Layer III, 128 kbps, 44.1 kHz, no padding: 417-byte frames of 1152 samples (~26.12 ms each).
    return (b"\xff\xfb\x90\x00" + b"\x00" * 413) * frames


def make_png(width: int = 1200, height: int = 900) -> bytes:
    buffer = io.BytesIO()
    Image.effect_noise((width, height), 40).convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue()


def upload(name: str, data: bytes, content_type: str = "application/octet-stream") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, data, content_type=content_type)


def post_message(user, group, **fields):
    return _client(user).post(f"/api/v1/groups/{group.pk}/messages/", fields, format="multipart")


@pytest.fixture
def chat():
    admin, student, group = _build_chat_fixture()
    return admin, student, group


# ---------------------------------------------------------------------------------------------------------------------
# Voice notes
# ---------------------------------------------------------------------------------------------------------------------


def test_voice_note_is_measured_by_the_server_and_exposes_metadata(chat):
    _admin, student, group = chat
    waveform = list(range(0, 96, 2))
    response = post_message(
        student,
        group,
        message_type="voice",
        attachment=upload("note.wav", make_wav(10)),
        duration_ms=9500,  # client hint; the server's own measurement wins
        waveform=json.dumps(waveform),
    )
    assert response.status_code == 201, response.data
    data = response.data["data"]
    assert data["message_type"] == "voice"
    meta = data["attachment_meta"]
    assert meta["kind"] == "voice"
    assert meta["mime"] == "audio/wav"
    assert meta["duration_ms"] == 10000
    assert meta["waveform"] == waveform
    assert meta["size"] > 0 and meta["etag"]
    assert meta["width"] is None
    assert data["has_attachment"] is True


def test_voice_note_just_inside_limit_with_tolerance_is_accepted(chat):
    _admin, student, group = chat
    response = post_message(
        student, group, message_type="voice", attachment=upload("note.wav", make_wav(300.4)), duration_ms=300400
    )
    assert response.status_code == 201, response.data


def test_voice_note_over_five_minutes_is_rejected_whatever_the_client_claims(chat):
    _admin, student, group = chat
    honest = post_message(
        student, group, message_type="voice", attachment=upload("long.wav", make_wav(305)), duration_ms=305000
    )
    liar = post_message(
        student, group, message_type="voice", attachment=upload("long.wav", make_wav(330)), duration_ms=12000
    )
    for response in (honest, liar):
        assert response.status_code == 400
        assert response.data["code"] == "VOICE_TOO_LONG"
        assert "5 دقائق" in response.data["errors"]["attachment"][0]
    assert not Message.objects.filter(group=group, message_type=MessageType.VOICE).exists()


def test_voice_hint_over_limit_fails_fast(chat):
    _admin, student, group = chat
    response = post_message(
        student, group, message_type="voice", attachment=upload("a.wav", make_wav(2)), duration_ms=999_999
    )
    assert response.status_code == 400 and response.data["code"] == "VOICE_TOO_LONG"


def test_voice_size_cap(chat, settings):
    _admin, student, group = chat
    settings.CHAT_VOICE_MAX_BYTES = 10_000
    response = post_message(student, group, message_type="voice", attachment=upload("a.wav", make_wav(5)))
    assert response.status_code == 400 and response.data["code"] == "AUDIO_TOO_LARGE"


def test_header_lying_about_duration_is_rejected(chat):
    _admin, student, group = chat
    # A wav whose header says ~1 s but which carries a megabyte of trailing data.
    response = post_message(
        student, group, message_type="voice", attachment=upload("lie.wav", make_wav(1, extra=1_200_000))
    )
    assert response.status_code == 400 and response.data["code"] == "AUDIO_INVALID"


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("fake.m4a", b"this is not audio at all" * 50),
        ("wrongext.mp3", make_wav(3)),  # real wav renamed to mp3
        ("script.m4a", b"<html><script>alert(1)</script></html>" * 30),
        ("truncated.ogg", b"OggS\x00\x02" + b"\x00" * 30),
        ("video.mp4", b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00" * 200),
    ],
    ids=["text-as-m4a", "wav-as-mp3", "html-as-m4a", "truncated-ogg", "empty-mp4"],
)
def test_non_audio_or_mismatched_files_are_rejected(chat, name, data):
    _admin, student, group = chat
    response = post_message(student, group, message_type="voice", attachment=upload(name, data, "audio/mp4"))
    assert response.status_code == 400 and response.data["code"] == "AUDIO_INVALID"


@pytest.mark.parametrize("waveform", ["[1,2,999]", "[-1]", "not json", json.dumps(list(range(129))), "[true]"])
def test_invalid_waveform_is_rejected(chat, waveform):
    _admin, student, group = chat
    response = post_message(
        student, group, message_type="voice", attachment=upload("a.wav", make_wav(2)), waveform=waveform
    )
    assert response.status_code == 400 and response.data["code"] == "WAVEFORM_INVALID"


def test_voice_requires_an_attachment(chat):
    _admin, student, group = chat
    response = _client(student).post(f"/api/v1/groups/{group.pk}/messages/", {"message_type": "voice"}, format="json")
    assert response.status_code == 400


# ---------------------------------------------------------------------------------------------------------------------
# Uploaded audio files
# ---------------------------------------------------------------------------------------------------------------------


def test_audio_file_has_no_duration_cap(chat):
    _admin, student, group = chat
    response = post_message(
        student, group, message_type="audio", attachment=upload("lecture.wav", make_wav(900)), content="Lecture 3"
    )
    assert response.status_code == 201, response.data
    meta = response.data["data"]["attachment_meta"]
    assert meta["kind"] == "audio" and meta["duration_ms"] == 900_000
    assert meta["name"] == "lecture.wav"


def test_audio_file_mp3_is_accepted(chat):
    _admin, student, group = chat
    response = post_message(
        student, group, message_type="audio", attachment=upload("doctor.mp3", make_mp3(400), "audio/mpeg")
    )
    assert response.status_code == 201, response.data
    meta = response.data["data"]["attachment_meta"]
    assert meta["mime"] == "audio/mpeg"
    assert 10_000 < meta["duration_ms"] < 11_000


def test_audio_file_size_cap(chat, settings):
    _admin, student, group = chat
    settings.CHAT_AUDIO_FILE_MAX_BYTES = 50_000
    response = post_message(student, group, message_type="audio", attachment=upload("big.wav", make_wav(20)))
    assert response.status_code == 400 and response.data["code"] == "AUDIO_TOO_LARGE"


def test_sniffing_recognises_containers_by_magic_bytes():
    assert sniff_audio_family(b"OggS" + b"\x00" * 20) == "ogg"
    assert sniff_audio_family(b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00") == "mp4"
    assert sniff_audio_family(b"ID3\x04" + b"\x00" * 20) == "mp3"
    assert sniff_audio_family(b"\xff\xf1\x50\x80" + b"\x00" * 20) == "aac"
    assert sniff_audio_family(b"\xff\xfb\x90\x00") == "mp3"
    assert sniff_audio_family(b"%PDF-1.4") is None


# ---------------------------------------------------------------------------------------------------------------------
# Images and documents
# ---------------------------------------------------------------------------------------------------------------------


def test_image_gets_dimensions_and_small_thumbnail(chat):
    _admin, student, group = chat
    response = post_message(student, group, message_type="image", attachment=upload("photo.png", make_png()))
    assert response.status_code == 201, response.data
    meta = response.data["data"]["attachment_meta"]
    assert (meta["width"], meta["height"]) == (1200, 900)
    assert meta["mime"] == "image/png" and meta["has_thumbnail"] is True
    message = Message.objects.get(pk=response.data["data"]["id"])
    with Image.open(message.attachment_thumb.open("rb")) as thumb:
        assert thumb.format == "WEBP" and max(thumb.size) <= 480
    assert message.attachment_thumb.size < message.attachment.size


def test_image_type_rejects_pdf_and_oversized_images(chat, settings):
    _admin, student, group = chat
    pdf = post_message(student, group, message_type="image", attachment=upload("a.pdf", b"%PDF-1.4\n" + b"x" * 200))
    assert pdf.status_code == 400
    settings.CHAT_IMAGE_MAX_PIXELS = 1000
    huge = post_message(student, group, message_type="image", attachment=upload("a.png", make_png(100, 100)))
    assert huge.status_code == 400 and huge.data["code"] == "IMAGE_TOO_LARGE"
    corrupt = post_message(student, group, message_type="image", attachment=upload("b.png", make_png()[:80]))
    assert corrupt.status_code == 400


def test_pdf_document_still_works(chat):
    _admin, student, group = chat
    response = post_message(
        student, group, message_type="file", attachment=upload("notes.pdf", b"%PDF-1.4\n" + b"x" * 500)
    )
    assert response.status_code == 201, response.data
    assert response.data["data"]["attachment_meta"]["mime"] == "application/pdf"
    assert response.data["data"]["attachment_meta"]["has_thumbnail"] is False


def test_text_message_cannot_carry_attachment(chat):
    _admin, student, group = chat
    response = post_message(
        student, group, message_type="text", content="hi", attachment=upload("a.png", make_png(50, 50))
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------------------------------------------------
# Protected delivery
# ---------------------------------------------------------------------------------------------------------------------


def _send(student, group, **fields) -> int:
    response = post_message(student, group, **fields)
    assert response.status_code == 201, response.data
    return response.data["data"]["id"]


def _ticket(user, group, message_id):
    response = _client(user).post(f"/api/v1/groups/{group.pk}/messages/{message_id}/attachment-ticket/")
    assert response.status_code == 201, response.data
    return response.data["data"]


def _path(url: str) -> str:
    return "/" + url.split("/", 3)[3]


def _body(response) -> bytes:
    return b"".join(response.streaming_content) if response.streaming else response.content


def test_stream_is_inline_protected_and_supports_ranges(chat):
    admin, student, group = chat
    raw = make_wav(20)
    message_id = _send(student, group, message_type="audio", attachment=upload("lec.wav", raw))
    ticket = _ticket(admin, group, message_id)  # staff with group-management rights can open it too
    assert ticket["download_allowed"] is False and ticket["supports_range"] is True and ticket["kind"] == "audio"
    client = _client(admin)

    full = client.get(_path(ticket["preview_url"]))
    assert full.status_code == 200
    assert _body(full) == raw
    assert full["Content-Type"] == "audio/wav"
    assert full["Content-Disposition"].startswith("inline")
    assert full["Accept-Ranges"] == "bytes"
    assert full["X-Content-Type-Options"] == "nosniff"
    assert "no-store" in full["Cache-Control"] and "private" in full["Cache-Control"]
    assert full["ETag"] == f'"{ticket["etag"]}"'

    part = client.get(_path(ticket["preview_url"]), HTTP_RANGE="bytes=100-199")
    assert part.status_code == 206
    assert part["Content-Range"] == f"bytes 100-199/{len(raw)}"
    assert _body(part) == raw[100:200]

    tail = client.get(_path(ticket["preview_url"]), HTTP_RANGE="bytes=-50")
    assert tail.status_code == 206 and _body(tail) == raw[-50:]

    open_ended = client.get(_path(ticket["preview_url"]), HTTP_RANGE=f"bytes={len(raw) - 10}-")
    assert open_ended.status_code == 206 and _body(open_ended) == raw[-10:]

    unsatisfiable = client.get(_path(ticket["preview_url"]), HTTP_RANGE=f"bytes={len(raw) + 5}-")
    assert unsatisfiable.status_code == 416
    assert unsatisfiable["Content-Range"] == f"bytes */{len(raw)}"

    stale_if_range = client.get(_path(ticket["preview_url"]), HTTP_RANGE="bytes=0-9", HTTP_IF_RANGE='"other"')
    assert stale_if_range.status_code == 200

    unchanged = client.get(_path(ticket["preview_url"]), HTTP_IF_NONE_MATCH=full["ETag"])
    assert unchanged.status_code == 304 and unchanged.content == b""


def test_audio_tickets_allow_many_range_requests(chat):
    _admin, student, group = chat
    voice_id = _send(student, group, message_type="voice", attachment=upload("v.wav", make_wav(3)))
    pdf_id = _send(student, group, message_type="file", attachment=upload("a.pdf", b"%PDF-1.4\n" + b"x" * 300))
    voice = MessageAttachmentAccessTicket.objects.get(
        token=_ticket(student, group, voice_id)["preview_url"].split("/")[-2]
    )
    doc = MessageAttachmentAccessTicket.objects.get(token=_ticket(student, group, pdf_id)["preview_url"].split("/")[-2])
    assert voice.max_uses > doc.max_uses
    assert voice.expires_at - timezone.now() > timedelta(seconds=200)


def test_thumbnail_variant_is_small_webp_and_revalidates(chat):
    _admin, student, group = chat
    message_id = _send(student, group, message_type="image", attachment=upload("p.png", make_png()))
    ticket = _ticket(student, group, message_id)
    assert ticket["thumb_url"].endswith("?variant=thumb")
    client = _client(student)
    response = client.get(_path(ticket["thumb_url"]))
    assert response.status_code == 200 and response["Content-Type"] == "image/webp"
    body = _body(response)
    with Image.open(io.BytesIO(body)) as image:
        assert max(image.size) <= 480
    again = client.get(_path(ticket["thumb_url"]), HTTP_IF_NONE_MATCH=response["ETag"])
    assert again.status_code == 304
    assert client.get(_path(ticket["preview_url"]) + "?variant=huge").status_code == 404


def test_thumbnail_is_built_lazily_for_older_messages(chat):
    _admin, student, group = chat
    message = Message.objects.create(
        group=group,
        sender=student,
        message_type=MessageType.IMAGE,
        attachment=upload("old.png", make_png(900, 600)),
    )
    assert not message.attachment_thumb and not message.attachment_mime
    ticket = _ticket(student, group, message.pk)
    assert ticket["thumb_url"]
    response = _client(student).get(_path(ticket["thumb_url"]))
    assert response.status_code == 200
    message.refresh_from_db()
    assert message.attachment_thumb and message.attachment_width == 900


def test_non_images_have_no_thumbnail(chat):
    _admin, student, group = chat
    message_id = _send(student, group, message_type="voice", attachment=upload("v.wav", make_wav(2)))
    ticket = _ticket(student, group, message_id)
    assert ticket["thumb_url"] is None
    assert _client(student).get(_path(ticket["preview_url"]) + "?variant=thumb").status_code == 404


def test_outsiders_and_other_users_cannot_open_attachments(chat):
    _admin, student, group = chat
    message_id = _send(student, group, message_type="image", attachment=upload("p.png", make_png(100, 100)))
    outsider = User.objects.create_user(
        full_name="Outsider",
        email="outsider@example.test",
        phone_number="+963990000077",
        password="StrongPass123!",
        role=UserRole.STUDENT,
    )
    university = University.objects.create(name="Other University", code="OTH")
    StudentProfile.objects.create(
        user=outsider, university=university, verification_status=StudentVerificationStatus.APPROVED
    )
    assert (
        _client(outsider).post(f"/api/v1/groups/{group.pk}/messages/{message_id}/attachment-ticket/").status_code == 403
    )
    batch = _client(outsider).post(
        f"/api/v1/groups/{group.pk}/messages/attachment-tickets/", {"message_ids": [message_id]}, format="json"
    )
    assert batch.status_code == 403

    ticket = _ticket(student, group, message_id)
    # A leaked URL is useless to anyone but the member it was issued to.
    assert _client(outsider).get(_path(ticket["preview_url"])).status_code == 404
    # ... and to nobody once they leave the group.
    GroupMembership.objects.filter(group=group, user=student).update(status=GroupMembershipStatus.LEFT)
    assert _client(student).get(_path(ticket["preview_url"])).status_code in {403, 404}
    assert APIClient().get(_path(ticket["preview_url"])).status_code in {401, 403}


def test_expired_revoked_exhausted_and_deleted_tickets_fail(chat):
    _admin, student, group = chat
    message_id = _send(student, group, message_type="image", attachment=upload("p.png", make_png(100, 100)))
    client = _client(student)

    ticket = _ticket(student, group, message_id)
    token = ticket["preview_url"].split("/")[-2]
    MessageAttachmentAccessTicket.objects.filter(token=token).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert client.get(_path(ticket["preview_url"])).status_code == 404

    ticket = _ticket(student, group, message_id)
    token = ticket["preview_url"].split("/")[-2]
    MessageAttachmentAccessTicket.objects.filter(token=token).update(use_count=100)
    assert client.get(_path(ticket["preview_url"])).status_code == 404

    ticket = _ticket(student, group, message_id)
    Message.objects.filter(pk=message_id).update(is_deleted=True)
    assert client.get(_path(ticket["preview_url"])).status_code == 404


def test_batch_tickets_cover_a_screen_of_thumbnails(chat):
    _admin, student, group = chat
    ids = [
        _send(student, group, message_type="image", attachment=upload(f"p{i}.png", make_png(60, 60))) for i in range(3)
    ]
    text_id = _send(student, group, message_type="text", content="no attachment")
    response = _client(student).post(
        f"/api/v1/groups/{group.pk}/messages/attachment-tickets/",
        {"message_ids": [*ids, text_id, 999999]},
        format="json",
    )
    assert response.status_code == 201, response.data
    data = response.data["data"]
    assert [item["message_id"] for item in data["tickets"]] == ids
    assert all(item["thumb_url"] and item["download_allowed"] is False for item in data["tickets"])
    assert sorted(data["missing"]) == sorted([text_id, 999999])

    too_many = _client(student).post(
        f"/api/v1/groups/{group.pk}/messages/attachment-tickets/", {"message_ids": list(range(1, 30))}, format="json"
    )
    assert too_many.status_code == 400


def test_other_groups_messages_are_not_reachable_through_this_group(chat):
    admin, student, group = chat
    message_id = _send(student, group, message_type="image", attachment=upload("p.png", make_png(60, 60)))
    other = Group.objects.create(name="Other", university=group.university, created_by=admin)
    GroupMembership.objects.create(group=other, user=student, status=GroupMembershipStatus.APPROVED)
    response = _client(student).post(f"/api/v1/groups/{other.pk}/messages/{message_id}/attachment-ticket/")
    assert response.status_code == 404


def test_stored_filenames_are_random_not_client_chosen(chat):
    _admin, student, group = chat
    message_id = _send(student, group, message_type="voice", attachment=upload("../../etc/passwd.wav", make_wav(2)))
    message = Message.objects.get(pk=message_id)
    assert "passwd" not in message.attachment.name and ".." not in message.attachment.name
    assert message.attachment_name == "passwd.wav"


# ---------------------------------------------------------------------------------------------------------------------
# Moderation
# ---------------------------------------------------------------------------------------------------------------------


def test_staff_report_list_exposes_voice_attachment(chat):
    admin, student, group = chat
    message_id = _send(student, group, message_type="voice", attachment=upload("v.wav", make_wav(4)))
    reporter = User.objects.create_user(
        full_name="Reporter",
        email="reporter@example.test",
        phone_number="+963990000066",
        password="StrongPass123!",
        role=UserRole.STUDENT,
    )
    MessageReport.objects.create(message_id=message_id, reported_by=reporter, reason="abuse")
    response = _client(admin).get("/api/v1/dashboard/chat/reports/")
    assert response.status_code == 200, response.data
    payload = response.data["data"] if "data" in response.data else response.data
    rows = payload["results"] if isinstance(payload, dict) and "results" in payload else payload
    row = rows[0]
    assert row["message_type"] == "voice"
    assert row["attachment_meta"]["duration_ms"] == 4000
    assert row["attachment_ticket_endpoint"] == f"/api/v1/groups/{group.pk}/messages/{message_id}/attachment-ticket/"


# ---------------------------------------------------------------------------------------------------------------------
# Unit
# ---------------------------------------------------------------------------------------------------------------------


def test_parse_range():
    assert parse_range("bytes=0-9", 100) == (0, 9)
    assert parse_range("bytes=90-500", 100) == (90, 99)
    assert parse_range("bytes=-10", 100) == (90, 99)
    assert parse_range("bytes=-500", 100) == (0, 99)
    assert parse_range("bytes=100-", 100) == "unsatisfiable"
    assert parse_range("bytes=0-1,5-6", 100) is None  # multi-range: serve everything
    assert parse_range("items=0-1", 100) is None
    assert parse_range("bytes=9-3", 100) is None
