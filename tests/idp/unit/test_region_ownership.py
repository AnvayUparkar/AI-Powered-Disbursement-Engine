import pytest
from typing import List
from idp.models.table import TableRegion, TableStructure, TableCell
from idp.models.ocr import OCRResult, OCRElement
from idp.models.layout import LayoutElement, ElementType
from idp.models.processing import ProcessingMetrics
from idp.services.docling.parser import DoclingParseResult
from idp.services.fusion.region_mask import TableRegionMask
from idp.services.output.serializer import DocumentSerializer


def test_1_rapidocr_text_outside_docling_table():
    """TEST 1: RapidOCR text outside a Docling table should be included."""
    table_region = TableRegion(
        page_number=1,
        bbox=[10.0, 100.0, 500.0, 300.0],
        table_id="tbl-1"
    )
    rapidocr_bbox = [10.0, 10.0, 200.0, 50.0]  # Above table

    is_blocked, decision = TableRegionMask.is_inside_or_overlapping_table(rapidocr_bbox, [table_region])
    assert is_blocked is False
    assert decision == "ADDED_NON_TABLE_TEXT"


def test_2_rapidocr_text_completely_inside_docling_table():
    """TEST 2: RapidOCR text completely inside a Docling table should be skipped."""
    table_region = TableRegion(
        page_number=1,
        bbox=[10.0, 100.0, 500.0, 400.0],
        table_id="tbl-1"
    )
    rapidocr_bbox = [50.0, 150.0, 200.0, 200.0]  # Fully inside table

    is_blocked, decision = TableRegionMask.is_inside_or_overlapping_table(rapidocr_bbox, [table_region])
    assert is_blocked is True
    assert decision == "SKIPPED_INSIDE_DOCLING_TABLE"


def test_3_small_rapidocr_bbox_inside_large_table():
    """TEST 3: Small RapidOCR bbox inside a large table should be skipped even if IoU is very low."""
    table_region = TableRegion(
        page_number=1,
        bbox=[0.0, 0.0, 1000.0, 1000.0],  # Huge table area = 1,000,000
        table_id="tbl-large"
    )
    rapidocr_bbox = [10.0, 10.0, 20.0, 20.0]  # Tiny OCR area = 100, IoU ~ 0.0001

    is_blocked, decision = TableRegionMask.is_inside_or_overlapping_table(rapidocr_bbox, [table_region])
    assert is_blocked is True
    assert decision == "SKIPPED_INSIDE_DOCLING_TABLE"


def test_4_rapidocr_text_partially_overlapping_table():
    """TEST 4: RapidOCR text partially overlapping table (>= 40% area inside) should be skipped."""
    table_region = TableRegion(
        page_number=1,
        bbox=[100.0, 100.0, 500.0, 500.0],
        table_id="tbl-1"
    )
    # OCR box from x=0 to x=180, y=100 to y=200 (width=180, height=100, area=18,000)
    # Center x=90 < 100 (outside table)
    # Overlap region: x=100 to x=180, y=100 to y=200 (width=80, height=100, area=8,000)
    # Overlap ratio = 8,000 / 18,000 = 44.4% >= 40%
    rapidocr_bbox = [0.0, 100.0, 180.0, 200.0]

    is_blocked, decision = TableRegionMask.is_inside_or_overlapping_table(rapidocr_bbox, [table_region])
    assert is_blocked is True
    assert decision == "SKIPPED_OVERLAPPING_TABLE"


def test_5_docling_table_page_1_and_rapidocr_text_page_2():
    """TEST 5: Docling table on page 1 and RapidOCR text on page 2 should both be retained."""
    serializer = DocumentSerializer()

    docling_res = DoclingParseResult(
        elements=[],
        tables=[
            TableStructure(
                id="tbl-1",
                page_number=1,
                num_rows=1,
                num_cols=1,
                cells=[TableCell(row_index=0, col_index=0, text="Page 1 Table Cell", bbox=[10.0, 100.0, 200.0, 200.0])],
                bbox=[10.0, 100.0, 200.0, 200.0]
            )
        ],
        page_count=2,
        pages_dimensions=[{"width": 595.0, "height": 842.0}, {"width": 595.0, "height": 842.0}]
    )

    ocr_p2 = OCRElement(
        id="ocr-p2",
        text="Page 2 Independent Header",
        bbox=[10.0, 100.0, 200.0, 200.0],  # Same bbox as page 1 table, but on Page 2!
        confidence=0.95,
        page_number=2,
        line_number=1,
        source="ocr"
    )

    ocr_res = OCRResult(page_number=2, elements=[ocr_p2])

    parsed_doc = serializer.build_unified_document(
        doc_id="TEST-P1P2",
        filename="multipage.pdf",
        mime_type="application/pdf",
        file_size_bytes=2048,
        page_count=2,
        docling_result=docling_res,
        ocr_results=[ocr_res],
        vlm_corrections={},
        metrics=ProcessingMetrics()
    )

    # Page 1 table retained
    assert len(parsed_doc.tables) == 1
    assert parsed_doc.tables[0].page_number == 1
    assert parsed_doc.tables[0].cells[0].text == "Page 1 Table Cell"

    # Page 2 RapidOCR text retained (not blocked by page 1 table)
    p2_elements = [e for e in parsed_doc.elements if e.page_number == 2]
    assert len(p2_elements) == 1
    assert p2_elements[0].text == "Page 2 Independent Header"


def test_6_multiple_tables_on_same_page():
    """TEST 6: Multiple tables on the same page should protect all respective table regions."""
    table1 = TableRegion(page_number=1, bbox=[10.0, 10.0, 200.0, 100.0], table_id="t1")
    table2 = TableRegion(page_number=1, bbox=[10.0, 300.0, 200.0, 400.0], table_id="t2")
    tables = [table1, table2]

    # In table 1 -> Skipped
    b1, d1 = TableRegionMask.is_inside_or_overlapping_table([20.0, 20.0, 150.0, 80.0], tables)
    assert b1 is True

    # In table 2 -> Skipped
    b2, d2 = TableRegionMask.is_inside_or_overlapping_table([20.0, 320.0, 150.0, 380.0], tables)
    assert b2 is True

    # Between tables -> Included
    b3, d3 = TableRegionMask.is_inside_or_overlapping_table([20.0, 150.0, 150.0, 250.0], tables)
    assert b3 is False
    assert d3 == "ADDED_NON_TABLE_TEXT"


def test_7_multilingual_non_table_text():
    """TEST 7: Multilingual non-table text from RapidOCR should be retained cleanly."""
    serializer = DocumentSerializer()

    hindi_ocr = OCRElement(
        id="ocr-hi",
        text="आयकर विभाग INCOME TAX DEPARTMENT",
        bbox=[10.0, 10.0, 300.0, 40.0],
        confidence=0.96,
        page_number=1,
        line_number=1,
        source="ocr"
    )
    ocr_res = OCRResult(page_number=1, elements=[hindi_ocr])

    parsed_doc = serializer.build_unified_document(
        doc_id="TEST-MULTI",
        filename="pan.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        page_count=1,
        docling_result=None,
        ocr_results=[ocr_res],
        vlm_corrections={},
        metrics=ProcessingMetrics()
    )

    assert len(parsed_doc.elements) == 1
    assert "INCOME TAX DEPARTMENT" in parsed_doc.elements[0].text
    assert parsed_doc.elements[0].source == "RAPIDOCR"


def test_8_repeated_headers_across_multiple_pages():
    """TEST 8: Repeated headers across multiple pages must NOT be removed by page-scoped deduplication."""
    serializer = DocumentSerializer()

    hdr1 = OCRElement(
        id="hdr-p1",
        text="Confidential Financial Statement",
        bbox=[10.0, 10.0, 300.0, 30.0],
        confidence=0.99,
        page_number=1,
        line_number=1,
        source="ocr"
    )
    hdr2 = OCRElement(
        id="hdr-p2",
        text="Confidential Financial Statement",
        bbox=[10.0, 10.0, 300.0, 30.0],
        confidence=0.99,
        page_number=2,
        line_number=1,
        source="ocr"
    )

    ocr_res1 = OCRResult(page_number=1, elements=[hdr1])
    ocr_res2 = OCRResult(page_number=2, elements=[hdr2])

    parsed_doc = serializer.build_unified_document(
        doc_id="TEST-MULTI-HDR",
        filename="statement.pdf",
        mime_type="application/pdf",
        file_size_bytes=2048,
        page_count=2,
        docling_result=None,
        ocr_results=[ocr_res1, ocr_res2],
        vlm_corrections={},
        metrics=ProcessingMetrics()
    )

    p1_elems = [e for e in parsed_doc.elements if e.page_number == 1]
    p2_elems = [e for e in parsed_doc.elements if e.page_number == 2]

    assert len(p1_elems) == 1
    assert len(p2_elems) == 1
    assert p1_elems[0].text == "Confidential Financial Statement"
    assert p2_elems[0].text == "Confidential Financial Statement"
