"""Inspection of chat attachments: images, documents, voice notes and uploaded audio files.

Everything stored on a message is measured here from the bytes themselves. The client only *hints* (``duration_ms``,
``waveform``); a hint is never trusted for a limit. Content is identified by magic bytes, then parsed by a real
decoder (Pillow for images, mutagen for audio) so a renamed or truncated file is rejected rather than stored.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from rest_framework.exceptions import ValidationError

from apps.common.file_validation import (
    UploadPolicy,
    _read_head,
    detect_content_type,
    validate_document_upload,
    validate_uploaded_file,
)

from .models import MessageType

WAVEFORM_MAX_POINTS = 128
THUMBNAIL_QUALITY = 72

# Arabic, user-facing messages keyed by stable machine code.
MESSAGES = {
    "VOICE_TOO_LONG": "مدة الرسالة الصوتية تتجاوز الحد الأقصى المسموح (5 دقائق).",
    "AUDIO_INVALID": "ملف الصوت غير صالح أو غير مدعوم. الصيغ المدعومة: m4a, aac, mp3, ogg, opus, wav.",
    "AUDIO_TOO_LARGE": "حجم الملف الصوتي يتجاوز الحد المسموح.",
    "IMAGE_INVALID": "الصورة غير صالحة أو تالفة.",
    "IMAGE_TOO_LARGE": "حجم الصورة أو أبعادها تتجاوز الحد المسموح.",
    "WAVEFORM_INVALID": "بيانات الموجة الصوتية غير صالحة.",
    "ATTACHMENT_REQUIRED": "المرفق مطلوب لهذا النوع من الرسائل.",
}


class ChatMediaError(ValidationError):
    """A 400 whose ``code`` in the API envelope is a stable machine value (e.g. ``VOICE_TOO_LONG``)."""

    def __init__(self, code: str, field: str = "attachment", message: str | None = None):
        super().__init__({field: [message or MESSAGES.get(code, code)]})
        self.api_code = code


@dataclass
class ChatMediaInfo:
    name: str
    mime: str
    size: int
    sha256: str
    duration_ms: int | None = None
    waveform: list[int] | None = None
    width: int | None = None
    height: int | None = None
    thumb: bytes | None = None


# --------------------------------------------------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------------------------------------------------


def _clean_name(raw: str, fallback: str) -> str:
    name = re.sub(r"[\x00-\x1f\x7f\\/]+", "", Path(str(raw or "")).name).strip()
    return (name or fallback)[:255]


def _sha256(uploaded_file) -> str:
    digest = hashlib.sha256()
    uploaded_file.seek(0)
    for chunk in iter(lambda: uploaded_file.read(1024 * 1024), b""):
        digest.update(chunk)
    uploaded_file.seek(0)
    return digest.hexdigest()


def parse_waveform(value) -> list[int] | None:
    """Accept a list of ints, or (multipart) a JSON array string / comma-separated ints. ``None`` when absent."""

    if value in (None, "", []):
        return None
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "ignore")
    if isinstance(value, str):
        text = value.strip()
        try:
            value = (
                json.loads(text) if text.startswith("[") else [int(part) for part in text.split(",") if part.strip()]
            )
        except ValueError:
            raise ChatMediaError("WAVEFORM_INVALID", "waveform") from None
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= WAVEFORM_MAX_POINTS:
        raise ChatMediaError("WAVEFORM_INVALID", "waveform")
    points: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not 0 <= item <= 255:
            raise ChatMediaError("WAVEFORM_INVALID", "waveform")
        points.append(int(item))
    return points


# --------------------------------------------------------------------------------------------------------------------
# Images
# --------------------------------------------------------------------------------------------------------------------


def _image_policy() -> UploadPolicy:
    return UploadPolicy(
        allowed_extensions=frozenset({"png", "jpg", "jpeg", "webp"}),
        allowed_mime_types=frozenset({"image/png", "image/jpeg", "image/webp"}),
        max_size_bytes=int(settings.CHAT_IMAGE_MAX_BYTES),
    )


def _orientation_swaps(image) -> bool:
    try:
        return int(image.getexif().get(274, 1)) in (5, 6, 7, 8)
    except Exception:  # noqa: BLE001 - malformed EXIF must not fail an otherwise valid image
        return False


def make_thumbnail(fileobj, max_px: int | None = None) -> tuple[bytes, int, int]:
    """Return ``(webp_bytes, width, height)`` of the image as displayed (EXIF rotation applied)."""

    from PIL import Image, ImageOps

    max_px = int(max_px or settings.CHAT_THUMBNAIL_MAX_PX)
    fileobj.seek(0)
    with Image.open(fileobj) as image:
        width, height = image.size
        if _orientation_swaps(image):
            width, height = height, width
        if image.format == "JPEG":
            image.draft("RGB", (max_px * 2, max_px * 2))  # decode at reduced scale: much faster for large photos
        oriented = ImageOps.exif_transpose(image)
        oriented.thumbnail((max_px, max_px))
        final = oriented.convert("RGBA" if "A" in oriented.getbands() else "RGB")
        out = io.BytesIO()
        final.save(out, format="WEBP", quality=THUMBNAIL_QUALITY, method=4)
    fileobj.seek(0)
    return out.getvalue(), width, height


def _inspect_image(uploaded_file, info: ChatMediaInfo) -> None:
    from PIL import Image

    max_pixels = int(settings.CHAT_IMAGE_MAX_PIXELS)
    try:
        uploaded_file.seek(0)
        with Image.open(uploaded_file) as image:
            width, height = image.size
            if width <= 0 or height <= 0:
                raise ChatMediaError("IMAGE_INVALID")
            if width * height > max_pixels:
                raise ChatMediaError("IMAGE_TOO_LARGE")
        thumb, width, height = make_thumbnail(uploaded_file)
    except ChatMediaError:
        raise
    except Exception:  # noqa: BLE001 - any decoder failure means the file is not a usable image
        raise ChatMediaError("IMAGE_INVALID") from None
    info.width, info.height, info.thumb = width, height, thumb


# --------------------------------------------------------------------------------------------------------------------
# Audio
# --------------------------------------------------------------------------------------------------------------------

# family -> (canonical mime, allowed extensions)
_AUDIO_FAMILIES: dict[str, tuple[str, frozenset[str]]] = {
    "mp3": ("audio/mpeg", frozenset({"mp3"})),
    "aac": ("audio/aac", frozenset({"aac"})),
    "mp4": ("audio/mp4", frozenset({"m4a", "mp4", "m4b"})),
    "ogg": ("audio/ogg", frozenset({"ogg", "oga", "opus"})),
    "wav": ("audio/wav", frozenset({"wav"})),
}
_MP4_BRANDS = frozenset(
    {b"M4A ", b"M4B ", b"mp42", b"mp41", b"isom", b"iso2", b"iso4", b"iso5", b"dash", b"3gp4", b"3gp5"}
)
#: Highest believable byte rate (uncompressed 16-bit stereo 48 kHz wav is ~192 KB/s); a header claiming far more
#: bytes per second than that is lying about its duration.
_MAX_BYTES_PER_SECOND = 400_000


def sniff_audio_family(head: bytes) -> str | None:
    """Identify the audio container from magic bytes only."""

    if head[:4] == b"OggS":
        return "ogg"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[4:8] == b"ftyp" and head[8:12] in _MP4_BRANDS:
        return "mp4"
    if head[:3] == b"ID3":
        return "mp3"
    if len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0:
        # ADTS (AAC) frames have layer bits 00; MPEG audio layers I-III never do.
        return "aac" if head[1] & 0x06 == 0 else "mp3"
    return None


def _length(parsed) -> float:
    if parsed.info is None:
        raise ChatMediaError("AUDIO_INVALID")
    return float(parsed.info.length)


def _mp4_has_audio_track(raw) -> bool:
    """True when the ISO base media file has a ``soun`` track, found by walking the boxes ourselves.

    mutagen cannot read the codec of files written by Android's MediaRecorder (the QuickTime-style ``meta`` box with
    no version/flags field makes it lose the ``trak`` sibling), so ``info.codec`` is empty for every phone recording.
    """

    def boxes(buf: bytes):
        pos = 0
        while pos + 8 <= len(buf):
            size = int.from_bytes(buf[pos : pos + 4], "big")
            kind = buf[pos + 4 : pos + 8]
            header = 8
            if size == 1:
                size = int.from_bytes(buf[pos + 8 : pos + 16], "big")
                header = 16
            elif size == 0:
                size = len(buf) - pos
            if size < header or pos + size > len(buf):
                return
            yield kind, buf[pos + header : pos + size]
            pos += size

    try:
        raw.seek(0)
        top = 0
        while True:
            raw.seek(top)
            head = raw.read(16)
            if len(head) < 8:
                return False
            size = int.from_bytes(head[:4], "big")
            kind = head[4:8]
            header = 8
            if size == 1 and len(head) == 16:
                size = int.from_bytes(head[8:16], "big")
                header = 16
            if size < header:  # size 0 (to end of file) is only legal for the last box, which is never moov here
                return False
            if kind == b"moov":
                if size > 8 * 1024 * 1024:
                    return False
                raw.seek(top + header)
                moov = raw.read(size - header)
                for child, body in boxes(moov):
                    if child != b"trak":
                        continue
                    for tchild, tbody in boxes(body):
                        if tchild != b"mdia":
                            continue
                        for mchild, mbody in boxes(tbody):
                            if mchild == b"hdlr" and mbody[8:12] == b"soun":
                                return True
                return False
            top += size
    except Exception:  # noqa: BLE001 - unreadable structure simply means "not proven audio"
        return False
    finally:
        try:
            raw.seek(0)
        except Exception:  # noqa: BLE001
            pass


def measure_audio_seconds(uploaded_file, family: str) -> float:
    """Real duration in seconds as parsed from the file. Raises ``ChatMediaError(AUDIO_INVALID)`` if unreadable."""

    import mutagen
    from mutagen.aac import AAC
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4
    from mutagen.oggopus import OggOpus
    from mutagen.oggvorbis import OggVorbis
    from mutagen.wave import WAVE

    raw = getattr(uploaded_file, "file", uploaded_file)
    try:
        raw.seek(0)
        if family == "mp3":
            length = _length(MP3(raw))
        elif family == "aac":
            length = _length(AAC(raw))
        elif family == "wav":
            length = _length(WAVE(raw))
        elif family == "mp4":
            mp4 = MP4(raw)
            codec = str(getattr(mp4.info, "codec", "") or "")
            if not codec.startswith(("mp4a", "alac")) and not _mp4_has_audio_track(raw):
                raise ChatMediaError("AUDIO_INVALID")  # a video or empty container, not audio
            length = _length(mp4)
        else:
            try:
                length = _length(OggOpus(raw))
            except mutagen.MutagenError:
                raw.seek(0)
                length = _length(OggVorbis(raw))
    except ChatMediaError:
        raise
    except Exception:  # noqa: BLE001 - mutagen raises many types for corrupt/truncated input
        raise ChatMediaError("AUDIO_INVALID") from None
    finally:
        try:
            raw.seek(0)
        except Exception:  # noqa: BLE001
            pass
    if not length or length <= 0 or length != length:  # also rejects NaN
        raise ChatMediaError("AUDIO_INVALID")
    return float(length)


def _inspect_audio(uploaded_file, message_type: str, info: ChatMediaInfo, extension: str, hint_ms: int | None) -> None:
    is_voice = message_type == MessageType.VOICE
    max_bytes = int(settings.CHAT_VOICE_MAX_BYTES if is_voice else settings.CHAT_AUDIO_FILE_MAX_BYTES)
    if info.size > max_bytes:
        raise ChatMediaError("AUDIO_TOO_LARGE")

    limit_ms = int(settings.CHAT_VOICE_MAX_SECONDS) * 1000 + int(settings.CHAT_VOICE_DURATION_TOLERANCE_MS)
    if is_voice and hint_ms is not None and hint_ms > limit_ms:
        raise ChatMediaError("VOICE_TOO_LONG")  # honest client: fail before parsing anything

    family = sniff_audio_family(_read_head(uploaded_file, 64))
    if family is None or extension not in _AUDIO_FAMILIES[family][1]:
        raise ChatMediaError("AUDIO_INVALID")
    seconds = measure_audio_seconds(uploaded_file, family)
    if info.size > 64 * 1024 and info.size / seconds > _MAX_BYTES_PER_SECOND:
        raise ChatMediaError("AUDIO_INVALID")  # header claims a far shorter duration than the data can hold
    duration_ms = max(1, round(seconds * 1000))
    if is_voice and duration_ms > limit_ms:
        raise ChatMediaError("VOICE_TOO_LONG")
    info.mime = _AUDIO_FAMILIES[family][0]
    info.duration_ms = duration_ms


# --------------------------------------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------------------------------------


def inspect_attachment(
    uploaded_file, message_type: str, *, duration_ms: int | None = None, waveform=None
) -> ChatMediaInfo:
    """Validate an upload for ``message_type`` and measure everything the message will store."""

    if not uploaded_file:
        raise ChatMediaError("ATTACHMENT_REQUIRED")
    extension = Path(getattr(uploaded_file, "name", "")).suffix.lower().lstrip(".")
    size = int(getattr(uploaded_file, "size", 0) or 0)
    fallback = f"attachment.{extension}" if extension else "attachment"
    info = ChatMediaInfo(
        name=_clean_name(getattr(uploaded_file, "name", ""), fallback),
        mime="application/octet-stream",
        size=size,
        sha256="",
    )

    if message_type in {MessageType.VOICE, MessageType.AUDIO}:
        if size <= 0:
            raise ChatMediaError("AUDIO_INVALID")
        _inspect_audio(uploaded_file, message_type, info, extension, duration_ms)
        info.waveform = parse_waveform(waveform)
    else:
        if message_type == MessageType.IMAGE:
            validate_uploaded_file(uploaded_file, _image_policy(), "attachment")
        else:
            validate_document_upload(uploaded_file, "attachment")
        head = _read_head(uploaded_file)
        info.mime = detect_content_type(head, extension) or "application/octet-stream"
        if info.mime.startswith("image/"):
            _inspect_image(uploaded_file, info)
    info.sha256 = _sha256(uploaded_file)
    return info
