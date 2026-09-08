"""
Tests for OCRTableDetector.cluster_elements_into_rows and
LogicalTableSegmenter.is_section_header_row fixes.

Covers the PhonePe bank statement fragmentation bugs:
  - Anchor-based Y clustering (no running-average drift)
  - Orphan single-cell continuation row merging
  - Section header false-positive rejection (DEBIT, PM, 07:21)
"""
import pytest
from typing import List

from idp.models.layout import LayoutElement, ElementType
from idp.services.table_processing.ocr_table_detector import OCRTableDetector
from idp.services.table_processing.logical_segmenter import LogicalTableSegmenter
from idp.services.table_processing.models import RawRow, RawCell


def _make_elem(text: str, x0: float, y0: float, x1: float, y1: float, eid: str = "") -> LayoutElement:
    return LayoutElement(
        id=eid or text[:8],
        type=ElementType.TEXT,
        text=text,
        bbox=[x0, y0, x1, y1],
        confidence=0.95,
        page_number=1,
        source="rapidocr",
    )


def _make_row(texts: List[str]) -> RawRow:
    cells = [RawCell(text=t, column_index=i) for i, t in enumerate(texts)]
    return RawRow(cells=cells, row_index=0)


# ---------------------------------------------------------------------------
# cluster_elements_into_rows - Happy Path
# ---------------------------------------------------------------------------

def test_cluster_basic_two_rows():
    elems = [
        _make_elem("Date",   0.1, 0.10, 0.25, 0.13),
        _make_elem("DEBIT",  0.3, 0.10, 0.45, 0.13),
        _make_elem("Amount", 0.5, 0.10, 0.70, 0.13),
        _make_elem("Aug 22", 0.1, 0.15, 0.25, 0.18),
        _make_elem("CREDIT", 0.3, 0.15, 0.45, 0.18),
        _make_elem("500",    0.5, 0.15, 0.70, 0.18),
    ]
    rows = OCRTableDetector.cluster_elements_into_rows(elems)
    assert len(rows) == 2
    assert len(rows[0]) == 3
    assert len(rows[1]) == 3


# ---------------------------------------------------------------------------
# cluster_elements_into_rows - No Running-Average Drift
# ---------------------------------------------------------------------------

def test_anchor_y_no_drift():
    """4-element row whose Y-centres drift 0.005 each step stays as one row."""
    elems = [
        _make_elem("Aug 28",             0.05, 0.100, 0.20, 0.115),
        _make_elem("DEBIT",              0.25, 0.105, 0.40, 0.120),
        _make_elem("212",               0.45, 0.110, 0.60, 0.125),
        _make_elem("Paid to RANJIT",     0.65, 0.115, 0.95, 0.130),
    ]
    rows = OCRTableDetector.cluster_elements_into_rows(elems)
    assert len(rows) == 1, f"Expected 1 row but got {len(rows)}"
    assert len(rows[0]) == 4


# ---------------------------------------------------------------------------
# cluster_elements_into_rows - Orphan Merging
# ---------------------------------------------------------------------------

def test_orphan_single_cell_merged_into_predecessor():
    elems = [
        _make_elem("Sep 02, 2026 09:33", 0.05, 0.200, 0.30, 0.215, "date1"),
        _make_elem("DEBIT",              0.35, 0.200, 0.50, 0.215, "type1"),
        _make_elem("147",               0.55, 0.200, 0.70, 0.215, "amt1"),
        _make_elem("PM",                 0.05, 0.220, 0.15, 0.232, "orphan1"),
    ]
    rows = OCRTableDetector.cluster_elements_into_rows(elems)
    assert len(rows) == 1, f"Expected 1 merged row but got {len(rows)}"
    texts = [e.text for e in rows[0]]
    assert "PM" in texts


def test_orphan_not_merged_when_first_row():
    elems = [
        _make_elem("PM",            0.05, 0.100, 0.15, 0.112, "orphan_first"),
        _make_elem("Sep 01, 2026",  0.05, 0.200, 0.30, 0.215, "date2"),
        _make_elem("DEBIT",         0.35, 0.200, 0.50, 0.215, "type2"),
        _make_elem("161",           0.55, 0.200, 0.70, 0.215, "amt2"),
    ]
    rows = OCRTableDetector.cluster_elements_into_rows(elems)
    assert len(rows) == 2
    assert any(e.text == "PM" for e in rows[0])


# ---------------------------------------------------------------------------
# cluster_elements_into_rows - Edge Cases
# ---------------------------------------------------------------------------

def test_empty_elements_returns_empty():
    assert OCRTableDetector.cluster_elements_into_rows([]) == []


def test_elements_without_bbox_skipped():
    elems = [
        LayoutElement(id="bad", type=ElementType.TEXT, text="X", bbox=None,
                      confidence=0.9, page_number=1, source="ocr"),
        _make_elem("Valid", 0.1, 0.1, 0.5, 0.15),
    ]
    rows = OCRTableDetector.cluster_elements_into_rows(elems)
    assert len(rows) == 1
    assert rows[0][0].text == "Valid"


def test_elements_without_text_skipped():
    elems = [
        LayoutElement(id="empty", type=ElementType.TEXT, text="  ",
                      bbox=[0.1, 0.1, 0.5, 0.15], confidence=0.9,
                      page_number=1, source="ocr"),
        _make_elem("NonEmpty", 0.1, 0.1, 0.5, 0.15),
    ]
    rows = OCRTableDetector.cluster_elements_into_rows(elems)
    assert len(rows) == 1
    assert rows[0][0].text == "NonEmpty"


# ---------------------------------------------------------------------------
# LogicalTableSegmenter.is_section_header_row - False-positive guards
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fragment", [
    "DEBIT", "CREDIT", "PM", "AM",
    "07:21", "09:33 AM",
    "147", "26",
])
def test_section_header_rejects_ocr_fragments(fragment: str):
    row = _make_row([fragment])
    assert not LogicalTableSegmenter.is_section_header_row(row), (
        f"'{fragment}' was incorrectly classified as a section header"
    )


@pytest.mark.parametrize("title", [
    "Loan Details",
    "Installment Details",
    "Transaction History",
    "Account Statement",
])
def test_section_header_accepts_real_titles(title: str):
    row = _make_row([title])
    assert LogicalTableSegmenter.is_section_header_row(row), (
        f"'{title}' should be classified as a section header"
    )


def test_section_header_rejects_multi_cell_row():
    row = _make_row(["Loan Details", "Some Value"])
    assert not LogicalTableSegmenter.is_section_header_row(row)


def test_section_header_rejects_short_single_word():
    row = _make_row(["Date"])  # 4 chars < _TITLE_MIN_CHARS(5)
    assert not LogicalTableSegmenter.is_section_header_row(row)
