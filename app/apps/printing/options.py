"""Print options that behave like the browser's print dialog: pages per sheet, two-sided, orientation, page range, collation.

Pure functions only (no database): the price quote, the simulation data sent to the apps and the print-ready PDF engine all use
them, so what the customer sees, what is charged and what comes out of the printer can never disagree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from math import ceil

from django.db import models
from rest_framework.exceptions import ValidationError

PAGES_PER_SHEET_CHOICES = (1, 2, 4, 6, 9, 16)
MAX_PAGE_RANGE_LENGTH = 255


class PrintOrientation(models.TextChoices):
    AUTO = "auto", "Automatic"
    PORTRAIT = "portrait", "Portrait"
    LANDSCAPE = "landscape", "Landscape"


class DuplexFlip(models.TextChoices):
    LONG_EDGE = "long_edge", "Flip on the long edge"
    SHORT_EDGE = "short_edge", "Flip on the short edge"


class NUpDirection(models.TextChoices):
    LTR = "ltr", "Left to right"
    RTL = "rtl", "Right to left"


#: columns x rows of the grid on a PORTRAIT sheet for each pages-per-sheet value.
_PORTRAIT_GRID = {1: (1, 1), 2: (1, 2), 4: (2, 2), 6: (2, 3), 9: (3, 3), 16: (4, 4)}
#: Sheets that look best turned sideways when the customer leaves the orientation on "automatic" (2 and 6 per sheet).
_AUTO_LANDSCAPE = {2, 6}

_RANGE_PART = re.compile(r"^\s*(\d+)\s*(?:[-–—]\s*(\d+))?\s*$")


def parse_page_range(text: str | None, total_pages: int) -> list[int]:
    """Turn ``"1-5, 8"`` into the 1-based page numbers to print, in the order typed (duplicates removed).

    An empty text means every page. Pages outside the document, reversed ranges and anything that is not digits, commas and dashes are
    rejected with a message the customer can act on.
    """

    cleaned = (text or "").strip()
    if not cleaned:
        return list(range(1, total_pages + 1))
    if len(cleaned) > MAX_PAGE_RANGE_LENGTH:
        raise ValidationError({"page_range": "The page range is too long."})
    pages: list[int] = []
    seen: set[int] = set()
    for part in cleaned.replace("،", ",").replace(";", ",").split(","):
        if not part.strip():
            continue
        match = _RANGE_PART.match(part)
        if not match:
            raise ValidationError({"page_range": "Write the pages like 1-5, 8."})
        first = int(match.group(1))
        last = int(match.group(2) or first)
        if first < 1 or last < first:
            raise ValidationError({"page_range": "A page range must go from a smaller to a larger page."})
        if last > total_pages:
            raise ValidationError({"page_range": f"The document has only {total_pages} pages."})
        for page in range(first, last + 1):
            if page not in seen:
                seen.add(page)
                pages.append(page)
    if not pages:
        raise ValidationError({"page_range": "Write the pages like 1-5, 8."})
    return pages


@dataclass(frozen=True)
class SheetLayout:
    """How the pages sit on one side of a sheet."""

    pages_per_sheet: int
    columns: int
    rows: int
    landscape: bool
    direction: str

    def as_dict(self) -> dict:
        return {
            "pages_per_sheet": self.pages_per_sheet,
            "columns": self.columns,
            "rows": self.rows,
            "sheet_orientation": "landscape" if self.landscape else "portrait",
            "direction": self.direction,
        }


def sheet_layout(
    pages_per_sheet: int, orientation: str = PrintOrientation.AUTO, direction: str = NUpDirection.LTR
) -> SheetLayout:
    if pages_per_sheet not in _PORTRAIT_GRID:
        raise ValidationError({"pages_per_sheet": "Choose 1, 2, 4, 6, 9 or 16 pages per sheet."})
    columns, rows = _PORTRAIT_GRID[pages_per_sheet]
    if orientation == PrintOrientation.LANDSCAPE:
        landscape = True
    elif orientation == PrintOrientation.PORTRAIT:
        landscape = False
    else:
        landscape = pages_per_sheet in _AUTO_LANDSCAPE
    if landscape:
        # A landscape sheet has the long side horizontal, so the grid is turned a quarter: more columns than rows.
        columns, rows = max(columns, rows), min(columns, rows)
    else:
        columns, rows = min(columns, rows), max(columns, rows)
    if pages_per_sheet == 1:
        columns = rows = 1
    return SheetLayout(pages_per_sheet, columns, rows, landscape, direction)


def sides_needed(selected_pages: int, pages_per_sheet: int) -> int:
    """Printed sides (one face of a sheet each) one copy needs."""

    return max(ceil(selected_pages / pages_per_sheet), 1) if selected_pages else 0


def sheets_per_copy(selected_pages: int, pages_per_sheet: int, double_sided: bool) -> int:
    """Physical sheets of paper for one copy: sides, halved when both faces are used, rounded up."""

    sides = sides_needed(selected_pages, pages_per_sheet)
    return ceil(sides / 2) if double_sided else sides
