"""Build a PDF from a library file's protected page images, one page in memory at a time.

``FileResource`` keeps only the original upload (often DOCX/PPTX/image) and per-page PNGs, never the converted PDF, so
the offline package plaintext is generated from the same rendered pages the online viewer serves. Pillow's own
multi-page PDF writer keeps every decoded page alive until the end (gigabytes for 500 pages), so this is a small
sequential writer: each page is loaded, converted to JPEG (``DCTDecode``) and appended to ``out``; peak memory is one
page. Output is a plain, script-free PDF 1.4 (images only: no JavaScript, forms, links or embedded files).
"""

from __future__ import annotations

import io
from typing import IO

from PIL import Image

#: Rendered pages are ~150 dpi screenshots; 72/150 maps pixels to PDF points.
POINTS_PER_PIXEL = 72 / 150


class PdfTooLarge(Exception):
    pass


def _jpeg(image: Image.Image, quality: int) -> tuple[bytes, str]:
    if image.mode in ("RGBA", "LA", "P"):
        rgba = image.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.getchannel("A"))
        image = flat
    mode = "L" if image.mode in ("L", "1") else "RGB"
    image = image.convert(mode)
    buffer = io.BytesIO()
    kwargs = {"subsampling": 0} if mode == "RGB" else {}
    image.save(buffer, "JPEG", quality=quality, optimize=True, **kwargs)
    return buffer.getvalue(), "/DeviceGray" if mode == "L" else "/DeviceRGB"


def write_pdf_from_pages(pages, out: IO[bytes], *, quality: int = 82, max_bytes: int | None = None) -> int:
    """Write the PDF of ``pages`` (iterable of ``FilePage`` in order) to ``out``; returns the page count.

    Raises ``ValueError`` when there is no readable page and ``PdfTooLarge`` when ``max_bytes`` is exceeded.
    """

    offsets: list[int] = []
    position = 0

    def emit(data: bytes) -> None:
        nonlocal position
        out.write(data)
        position += len(data)

    def start_object() -> int:
        offsets.append(position)
        return len(offsets)  # object ids are 1-based; ids 1 and 2 are reserved for catalog and page tree

    emit(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets.extend([0, 0])  # placeholders for the catalog (1) and page tree (2), written last
    page_ids: list[int] = []
    for page in pages:
        with page.rendered_file.open("rb") as handle, Image.open(handle) as opened:
            opened.load()
            width, height = opened.size
            data, colour_space = _jpeg(opened, quality)
        image_id = start_object()
        emit(
            f"{image_id} 0 obj\n<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
            f"/ColorSpace {colour_space} /BitsPerComponent 8 /Filter /DCTDecode /Length {len(data)} >>\nstream\n".encode()
        )
        emit(data)
        emit(b"\nendstream\nendobj\n")
        del data
        pt_width, pt_height = width * POINTS_PER_PIXEL, height * POINTS_PER_PIXEL
        content = f"q {pt_width:.2f} 0 0 {pt_height:.2f} 0 0 cm /Im0 Do Q".encode()
        content_id = start_object()
        emit(
            f"{content_id} 0 obj\n<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream\nendobj\n"
        )
        page_id = start_object()
        emit(
            f"{page_id} 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {pt_width:.2f} {pt_height:.2f}] "
            f"/Resources << /XObject << /Im0 {image_id} 0 R >> >> /Contents {content_id} 0 R >>\nendobj\n".encode()
        )
        page_ids.append(page_id)
        if max_bytes is not None and position > max_bytes:
            raise PdfTooLarge()
    if not page_ids:
        raise ValueError("The file has no pages.")
    offsets[1] = position
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    emit(f"2 0 obj\n<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>\nendobj\n".encode())
    offsets[0] = position
    emit(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    xref_at = position
    emit(f"xref\n0 {len(offsets) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        emit(f"{offset:010d} 00000 n \n".encode())
    emit(f"trailer\n<< /Size {len(offsets) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode())
    if max_bytes is not None and position > max_bytes:
        raise PdfTooLarge()
    return len(page_ids)
