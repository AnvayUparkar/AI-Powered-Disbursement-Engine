"""
Tests for CombGridDetector -- printed comb-box field recovery from a page
image (row-signature predicate, glyph-band segmentation, and the high-precision
per-field recovery gate).
"""

import io

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from idp.services.extraction.comb_grid_detector import CombGridDetector
from idp.models.layout import LayoutElement, ElementType


# --------------------------------------------------------------------------- #
# predicate / value-run helpers                                                #
# --------------------------------------------------------------------------- #
def test_is_fused_comb_row_predicate():
    is_row = CombGridDetector.is_fused_comb_row
    assert is_row("Application Date:06082026 ApplINo.:APPL00343265",
                  [40.0, 780.0, 460.0, 800.0]) is True
    assert is_row("GSTIN No. 08AOOPK6924P1ZZ", [40.0, 600.0, 520.0, 620.0]) is True
    # all-caps heading, no digit-bearing run
    assert is_row("APPLICANT DETAILS", [40.0, 700.0, 240.0, 720.0]) is False
    # bare value, no label part
    assert is_row("06082026", [200.0, 780.0, 320.0, 800.0]) is False
    # not a wide band (w/h < 6)
    assert is_row("Date:06082026", [40.0, 780.0, 110.0, 800.0]) is False
    assert is_row(None, None) is False


def test_value_runs():
    assert CombGridDetector.value_runs(
        "Application Date:06082026 ApplINo.:APPL00343265"
    ) == ["06082026", "APPL00343265"]
    assert CombGridDetector.value_runs("APPLICANT DETAILS") == []
    assert CombGridDetector.value_runs("06082026") == []      # whole string == run
    assert CombGridDetector.value_runs("Ref ABCDEF") == []     # run has < 2 digits


# --------------------------------------------------------------------------- #
# glyph-band segmentation                                                      #
# --------------------------------------------------------------------------- #
def _digits_row_gray(text="06082026", cell_w=44, cell_h=60, margin=16) -> np.ndarray:
    """A clean comb row: evenly-spaced digits, no printed rules."""
    w = margin * 2 + cell_w * len(text)
    h = cell_h + margin * 2
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", cell_h - 6)
    except Exception:
        font = ImageFont.load_default()
    for i, ch in enumerate(text):
        x = margin + i * cell_w + cell_w // 4
        d.text((x, margin), ch, fill=0, font=font)
    return np.asarray(img)


def test_segment_glyph_bands_finds_digit_run():
    gray = _digits_row_gray("06082026")
    bands = CombGridDetector.segment_glyph_bands(gray)
    assert len(bands) == 1
    boxes = bands[0]
    # ~8 glyph components, left-to-right, non-overlapping-ish
    assert 6 <= len(boxes) <= 12
    xs = [b[0] for b in boxes]
    assert xs == sorted(xs)


def test_segment_glyph_bands_none_on_blank():
    assert CombGridDetector.segment_glyph_bands(np.full((60, 400), 255, np.uint8)) == []


def test_segment_glyph_bands_ignores_bad_input():
    assert CombGridDetector.segment_glyph_bands(None) == []
    assert CombGridDetector.segment_glyph_bands(np.zeros((4, 4, 3), np.uint8)) == []


# --------------------------------------------------------------------------- #
# _run_is_clean gate                                                           #
# --------------------------------------------------------------------------- #
def test_run_is_clean_accepts_uniform_rejects_scattered():
    uniform = [(i * 20.0, 0.0, i * 20.0 + 14.0, 30.0) for i in range(8)]
    assert CombGridDetector._run_is_clean(uniform) is True

    scattered = [(0.0, 0.0, 14.0, 30.0), (18.0, 0.0, 32.0, 30.0),
                 (120.0, 0.0, 150.0, 30.0), (300.0, 0.0, 305.0, 30.0)]
    assert CombGridDetector._run_is_clean(scattered) is False
    assert CombGridDetector._run_is_clean([(0.0, 0.0, 10.0, 10.0)]) is False


# --------------------------------------------------------------------------- #
# end-to-end recovery: image -> one PDF-point field element                    #
# --------------------------------------------------------------------------- #
def _page_png_with_digit_row(px_w, px_h, x0, y0, cell_w, text, glyph_w=26, glyph_h=48):
    """White page with `len(text)` evenly-spaced solid glyph blocks (a clean,
    deterministic stand-in for hand-written comb cells)."""
    img = Image.new("RGB", (px_w, px_h), "white")
    d = ImageDraw.Draw(img)
    for i in range(len(text)):
        gx = x0 + i * cell_w
        d.rectangle([gx, y0, gx + glyph_w, y0 + glyph_h], fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_recover_cell_elements_emits_one_field_in_pdf_points():
    pdf_w, pdf_h = 595.0, 200.0
    px_w, px_h = 1190, 400                    # 2x
    sx = px_w / pdf_w

    text = "06082026"
    cell_w = 60
    x0_px, y0_px = 220, 60          # 8 cells end at 700px (350pt), inside elem bbox
    png = _page_png_with_digit_row(px_w, px_h, x0_px, y0_px, cell_w, text)

    elem = LayoutElement(
        id="e-fused", type=ElementType.TEXT,
        text="Application Date:06082026 end",
        bbox=[40.0, 20.0, 460.0, 55.0],       # PDF points, spans label+cells
        confidence=0.35, page_number=1, reading_order=3,
        source="docling_ocr", structure_source="docling",
    )

    out = CombGridDetector().recover_cell_elements(
        image_bytes=png, elem=elem,
        page_px_w=px_w, page_px_h=px_h, pdf_w=pdf_w, pdf_h=pdf_h, doc_id="T",
    )

    assert len(out) == 1
    field = out[0]
    assert field.text == text
    assert field.source == "comb_box_merged"
    assert field.structure_source == "comb_grid"
    assert field.metadata.get("recovery") == "comb_grid"
    assert field.metadata.get("needs_vlm") is True
    assert field.metadata.get("num_cells") == len(text)

    # Field box sits around the digit run in PDF points (digits start ~x0_px).
    assert field.bbox[0] == pytest.approx(x0_px / sx, abs=25)
    assert field.bbox[2] > field.bbox[0]
    assert 40.0 <= field.bbox[0] and field.bbox[2] <= 460.0 + 5


def test_recover_cell_elements_no_run_returns_empty():
    png = _page_png_with_digit_row(400, 160, 40, 20, 40, "12345678")
    elem = LayoutElement(
        id="e1", type=ElementType.TEXT, text="APPLICANT DETAILS HEADER",
        bbox=[10.0, 10.0, 180.0, 25.0],
        confidence=0.9, page_number=1, source="docling_ocr", structure_source="docling",
    )
    assert CombGridDetector().recover_cell_elements(
        image_bytes=png, elem=elem,
        page_px_w=400, page_px_h=160, pdf_w=200.0, pdf_h=80.0, doc_id="T",
    ) == []


def test_recover_cell_elements_blank_image_returns_empty():
    blank = Image.new("RGB", (400, 160), "white")
    buf = io.BytesIO(); blank.save(buf, format="PNG")
    elem = LayoutElement(
        id="e1", type=ElementType.TEXT, text="Application Date:06082026 end",
        bbox=[10.0, 10.0, 180.0, 25.0],
        confidence=0.3, page_number=1, source="docling_ocr", structure_source="docling",
    )
    assert CombGridDetector().recover_cell_elements(
        image_bytes=buf.getvalue(), elem=elem,
        page_px_w=400, page_px_h=160, pdf_w=200.0, pdf_h=80.0, doc_id="T",
    ) == []


def test_document_processor_recover_comb_grids():
    from idp.services.document_processor import DocumentProcessor
    from idp.services.docling.parser import DoclingParseResult

    dp = DocumentProcessor()
    assert dp._recover_comb_grids(None, [], "T") == 0

    docling_res = DoclingParseResult(
        pages_dimensions=[{"width": 595.0, "height": 842.0}],
        elements=[
            LayoutElement(
                id="e1", type=ElementType.TEXT, text="APPLICANT DETAILS HEADER",
                bbox=[10.0, 10.0, 180.0, 25.0],
                confidence=0.9, page_number=1, source="docling_ocr", structure_source="docling",
            )
        ]
    )
    assert dp._recover_comb_grids(docling_res, [(b"fake_bytes", 595.0, 842.0)], "T") == 0

