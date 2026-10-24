"""The print-ready PDF engine: the plan (which pages on which face) and the rendered file (sizes, counts, order of copies)."""

from __future__ import annotations

import io

import pytest
from pypdf import PdfReader, PdfWriter
from rest_framework.exceptions import ValidationError

from apps.common.tests_phase3 import PNG_BYTES

from .pdf_engine import build_print_ready, plan_sides


def pdf(pages: int, width=595, height=842) -> io.BytesIO:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=width, height=height)
    buffer = io.BytesIO()
    writer.write(buffer)
    buffer.seek(0)
    return buffer


def plan(pages=None, **over):
    options: dict = {"pages_per_sheet": 1, "double_sided": False, "copies": 1, "collate": True}
    options.update(over)
    return plan_sides(pages or list(range(1, 11)), **options)


# ---- the plan -----------------------------------------------------------------------------------------------------------------


def test_one_up_one_sided_is_one_face_per_page():
    result = plan([1, 2, 3])
    assert result.sides == [[1], [2], [3]] and result.sheets == 3


def test_four_up_groups_pages_and_pads_the_last_face():
    result = plan(list(range(1, 11)), pages_per_sheet=4)
    assert result.sides == [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, None, None]]


def test_double_sided_pairs_faces_into_sheets_and_pads_an_odd_last_back():
    result = plan([1, 2, 3], double_sided=True)
    assert result.sides == [[1], [2], [3], [None]]
    assert result.sheets == 2 and result.blank_faces == {3}


def test_collated_copies_repeat_the_whole_document():
    result = plan([1, 2, 3], copies=2, collate=True)
    assert [face[0] for face in result.sides] == [1, 2, 3, 1, 2, 3]


def test_uncollated_copies_repeat_each_page():
    result = plan([1, 2, 3], copies=2, collate=False)
    assert [face[0] for face in result.sides] == [1, 1, 2, 2, 3, 3]


def test_uncollated_double_sided_repeats_each_sheet_not_each_face():
    result = plan([1, 2, 3, 4], double_sided=True, copies=2, collate=False)
    assert [face[0] for face in result.sides] == [1, 2, 1, 2, 3, 4, 3, 4]


def test_double_sided_copies_never_share_a_sheet():
    result = plan([1, 2, 3], double_sided=True, copies=2)  # 3 pages: the 2nd copy must start on a fresh sheet
    assert [face[0] for face in result.sides] == [1, 2, 3, None, 1, 2, 3, None]


def test_an_order_that_is_too_big_is_refused():
    with pytest.raises(ValidationError):
        plan(list(range(1, 400)), copies=99)


def test_the_page_count_matches_the_price_formula():
    # roadmap formula: ceil(selected / per sheet / 2) sheets per copy, times copies
    result = plan(list(range(1, 14)), pages_per_sheet=4, double_sided=True, copies=3)
    assert result.sheets == 2 * 3


# ---- the rendered file --------------------------------------------------------------------------------------------------------


def render(pages=6, **over):
    options: dict = {"paper_size": "A4"}
    options.update(over)
    data, summary = build_print_ready(pdf(pages), "doc.pdf", **options)
    return PdfReader(io.BytesIO(data)), summary


def size(page):
    return round(float(page.mediabox.width)), round(float(page.mediabox.height))


def test_one_up_keeps_the_paper_size_and_every_page():
    reader, summary = render(pages=3)
    assert len(reader.pages) == 3
    assert size(reader.pages[0]) == (595, 842)
    assert summary["faces"] == 3 and summary["sheets"] == 3


def test_two_up_uses_landscape_sheets():
    reader, summary = render(pages=4, pages_per_sheet=2)
    assert len(reader.pages) == 2 and size(reader.pages[0]) == (842, 595)
    assert summary["layout"]["sheet_orientation"] == "landscape"


def test_nine_up_and_sixteen_up_render():
    assert len(render(pages=20, pages_per_sheet=9)[0].pages) == 3
    assert len(render(pages=20, pages_per_sheet=16)[0].pages) == 2


def test_paper_sizes():
    assert size(render(pages=1, paper_size="A5")[0].pages[0]) == (420, 595)
    assert size(render(pages=1, paper_size="A3")[0].pages[0]) == (842, 1191)


def test_page_range_and_duplex_padding():
    reader, summary = render(pages=10, page_range="1-3", double_sided=True)
    assert len(reader.pages) == 4 and summary["blank_faces"] == 1 and summary["sheets"] == 2


def test_copies_collated_and_not():
    assert len(render(pages=3, copies=3)[0].pages) == 9
    assert len(render(pages=3, copies=3, collate=False)[0].pages) == 9


def test_four_pages_per_sheet_two_sided_three_copies_matches_the_quote():
    reader, summary = render(pages=12, pages_per_sheet=4, double_sided=True, copies=3)
    assert summary["sheets"] == 6 and len(reader.pages) == 12


def test_an_image_can_be_printed():
    data, summary = build_print_ready(io.BytesIO(PNG_BYTES), "photo.png", paper_size="A4")
    assert len(PdfReader(io.BytesIO(data)).pages) == 1 and summary["selected_pages"] == 1


def test_bad_range_is_a_validation_error():
    with pytest.raises(ValidationError):
        build_print_ready(pdf(3), "doc.pdf", paper_size="A4", page_range="9")


def test_rotated_source_pages_are_handled():
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=500)
    page.rotate(90)
    buffer = io.BytesIO()
    writer.write(buffer)
    buffer.seek(0)
    data, _ = build_print_ready(buffer, "doc.pdf", paper_size="A4", pages_per_sheet=4)
    assert len(PdfReader(io.BytesIO(data)).pages) == 1
