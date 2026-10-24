"""Builds the print-ready PDF of one order item: the chosen pages, several pages per sheet, copies in the right order.

``plan_sides`` is pure (which source pages go on which face of which sheet, in printing order) and is what the tests and the
staff screen rely on; ``build_print_ready`` renders that plan with pypdf. The printer then only has to print the file as it
is, two-sided if the order says so, so the result matches the simulation the customer saw.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from pypdf import PageObject, PdfReader, PdfWriter, Transformation
from pypdf.generic import DecodedStreamObject, NameObject
from rest_framework.exceptions import ValidationError

from .options import NUpDirection, SheetLayout, parse_page_range, sheet_layout

POINTS_PER_MM = 72 / 25.4
PAPER_POINTS = {  # portrait width x height in PostScript points
    "A5": (148 * POINTS_PER_MM, 210 * POINTS_PER_MM),
    "A4": (210 * POINTS_PER_MM, 297 * POINTS_PER_MM),
    "A3": (297 * POINTS_PER_MM, 420 * POINTS_PER_MM),
}
MAX_OUTPUT_SIDES = 20000
GRID_MARGIN = 18.0
GRID_GAP = 6.0


@dataclass(frozen=True)
class PrintPlan:
    """Faces to print, in order. ``sides[i]`` lists the 1-based source pages on face ``i`` (None = an empty cell)."""

    sides: list[list[int | None]]
    blank_faces: set[int]
    sheets: int
    layout: SheetLayout
    double_sided: bool

    @property
    def face_count(self) -> int:
        return len(self.sides)


def plan_sides(
    selected_pages: list[int],
    *,
    pages_per_sheet: int,
    double_sided: bool,
    copies: int,
    collate: bool,
    orientation: str = "auto",
    direction: str = NUpDirection.LTR,
) -> PrintPlan:
    layout = sheet_layout(pages_per_sheet, orientation, direction)
    faces: list[list[int | None]] = []
    for start in range(0, len(selected_pages), pages_per_sheet):
        chunk: list[int | None] = list(selected_pages[start : start + pages_per_sheet])
        chunk += [None] * (pages_per_sheet - len(chunk))
        faces.append(chunk)
    if not faces:
        raise ValidationError({"page_range": "There is nothing to print."})
    per_sheet = 2 if double_sided else 1
    sheets_of_copy = [faces[i : i + per_sheet] for i in range(0, len(faces), per_sheet)]
    if double_sided and len(sheets_of_copy[-1]) == 1:
        sheets_of_copy[-1].append(
            [None] * pages_per_sheet
        )  # the empty back of the last sheet keeps copies from sharing a sheet
    if collate:
        sheets = sheets_of_copy * copies
    else:
        sheets = [sheet for sheet in sheets_of_copy for _ in range(copies)]
    sides: list[list[int | None]] = []
    blank: set[int] = set()
    for sheet in sheets:
        for face in sheet:
            if all(cell is None for cell in face):
                blank.add(len(sides))
            sides.append(face)
    if len(sides) > MAX_OUTPUT_SIDES:
        raise ValidationError({"copies": "This order is too large to prepare in one file."})
    return PrintPlan(sides, blank, len(sheets), layout, double_sided)


def _source_reader(file_obj, filename: str) -> PdfReader:
    file_obj.seek(0)
    data = file_obj.read()
    if Path(filename).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
        from PIL import Image

        buffer = io.BytesIO()
        image = Image.open(io.BytesIO(data))
        image.convert("RGB").save(buffer, "PDF", resolution=150.0)
        data = buffer.getvalue()
    return PdfReader(io.BytesIO(data), strict=False)


def _cell_rects(layout: SheetLayout, sheet_w: float, sheet_h: float) -> list[tuple[float, float, float, float]]:
    """(x, y, width, height) of each cell in reading order (top-left first; right to left when asked)."""

    if layout.pages_per_sheet == 1:
        return [(0.0, 0.0, sheet_w, sheet_h)]
    cell_w = (sheet_w - 2 * GRID_MARGIN - (layout.columns - 1) * GRID_GAP) / layout.columns
    cell_h = (sheet_h - 2 * GRID_MARGIN - (layout.rows - 1) * GRID_GAP) / layout.rows
    rects = []
    for row in range(layout.rows):
        columns = list(range(layout.columns))
        if layout.direction == NUpDirection.RTL:
            columns.reverse()
        for column in columns:
            x = GRID_MARGIN + column * (cell_w + GRID_GAP)
            y = sheet_h - GRID_MARGIN - (row + 1) * cell_h - row * GRID_GAP
            rects.append((x, y, cell_w, cell_h))
    return rects


def _border(writer: PdfWriter, rects: list[tuple[float, float, float, float]]) -> DecodedStreamObject:
    commands = ["0.4 w", "0.55 G"]
    commands += [f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} re S" for x, y, w, h in rects]
    stream = DecodedStreamObject()
    stream.set_data("\n".join(commands).encode("ascii"))
    return stream


def build_print_ready(
    file_obj,
    filename: str,
    *,
    paper_size: str,
    page_range: str = "",
    pages_per_sheet: int = 1,
    double_sided: bool = False,
    copies: int = 1,
    collate: bool = True,
    orientation: str = "auto",
    direction: str = NUpDirection.LTR,
) -> tuple[bytes, dict]:
    """Render the print-ready file. Returns the PDF bytes and a summary (faces, sheets, layout) for the job record."""

    reader = _source_reader(file_obj, filename)
    total = len(reader.pages)
    # Pages attached to a writer can have their /Rotate baked into the content (needed before scaling them onto a sheet).
    source_pages = PdfWriter(clone_from=reader).pages
    selected = parse_page_range(page_range, total)
    plan = plan_sides(
        selected,
        pages_per_sheet=pages_per_sheet,
        double_sided=double_sided,
        copies=1,
        collate=True,
        orientation=orientation,
        direction=direction,
    )
    paper_w, paper_h = PAPER_POINTS[paper_size]
    sheet_w, sheet_h = (paper_h, paper_w) if plan.layout.landscape else (paper_w, paper_h)
    rects = _cell_rects(plan.layout, sheet_w, sheet_h)

    one_copy = PdfWriter()
    for face in plan.sides:
        sheet = one_copy.add_blank_page(width=sheet_w, height=sheet_h)
        for rect, source_number in zip(rects, face, strict=True):
            if source_number is None:
                continue
            source = source_pages[source_number - 1]
            source.transfer_rotation_to_content()
            page_w, page_h = float(source.mediabox.width), float(source.mediabox.height)
            x, y, cell_w, cell_h = rect
            scale = min(cell_w / page_w, cell_h / page_h)
            offset_x = x + (cell_w - page_w * scale) / 2 - float(source.mediabox.left) * scale
            offset_y = y + (cell_h - page_h * scale) / 2 - float(source.mediabox.bottom) * scale
            sheet.merge_transformed_page(source, Transformation().scale(scale, scale).translate(offset_x, offset_y))
        if plan.layout.pages_per_sheet > 1 and any(cell is not None for cell in face):
            frame = PageObject.create_blank_page(one_copy, sheet_w, sheet_h)
            frame[NameObject("/Contents")] = one_copy._add_object(_border(one_copy, rects))
            sheet.merge_page(frame)
    buffer = io.BytesIO()
    one_copy.write(buffer)
    single = buffer.getvalue()

    if copies == 1:
        output = single
        full_plan = plan
    else:
        full_plan = plan_sides(
            selected,
            pages_per_sheet=pages_per_sheet,
            double_sided=double_sided,
            copies=copies,
            collate=collate,
            orientation=orientation,
            direction=direction,
        )
        final = PdfWriter()
        if collate:
            for _ in range(copies):
                final.append(PdfReader(io.BytesIO(single)))
        else:
            per_sheet = 2 if double_sided else 1
            pages = PdfReader(io.BytesIO(single)).pages
            sheet_groups = [pages[i : i + per_sheet] for i in range(0, len(pages), per_sheet)]
            for group in sheet_groups:
                for _ in range(copies):
                    for page in group:
                        final.add_page(page)
        out = io.BytesIO()
        final.write(out)
        output = out.getvalue()
    summary = {
        "source_pages": total,
        "selected_pages": len(selected),
        "faces": full_plan.face_count,
        "sheets": full_plan.sheets,
        "blank_faces": len(full_plan.blank_faces),
        "layout": plan.layout.as_dict(),
        "paper_size": paper_size,
        "double_sided": double_sided,
        "copies": copies,
        "collate": collate,
    }
    return output, summary
