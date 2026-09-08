import pytest
from typing import List
from idp.models.table import TableStructure, TableCell, TableRegion
from idp.models.ocr import OCRResult, OCRElement
from idp.models.layout import LayoutElement, ElementType
from idp.models.processing import ProcessingMetrics
from idp.services.docling.parser import DoclingParseResult
from idp.services.fusion.spatial_fusion import SpatialCellFusion
from idp.services.output.serializer import DocumentSerializer


def test_spatial_cell_fusion_happy_path():
    """
    Happy Path: Docling TableFormer detects 2x2 grid with empty cell texts (do_ocr=False).
    RapidOCR provides text elements. SpatialCellFusion geometrically injects tokens
    into the corresponding cells and reconstructs rows_raw and headers.
    """
    # Grid:
    # Row 0, Col 0: [0.1, 0.1, 0.4, 0.2] -> "Sanctioned Amount"
    # Row 0, Col 1: [0.5, 0.1, 0.9, 0.2] -> "94111.00"
    # Row 1, Col 0: [0.1, 0.3, 0.4, 0.4] -> "Interest Rate"
    # Row 1, Col 1: [0.5, 0.3, 0.9, 0.4] -> "21.84%"
    table = TableStructure(
        id="tbl-kfs-1",
        page_number=1,
        num_rows=2,
        num_cols=2,
        cells=[
            TableCell(row_index=0, col_index=0, text="", bbox=[100.0, 100.0, 400.0, 200.0]),
            TableCell(row_index=0, col_index=1, text="", bbox=[500.0, 100.0, 900.0, 200.0]),
            TableCell(row_index=1, col_index=0, text="", bbox=[100.0, 300.0, 400.0, 400.0]),
            TableCell(row_index=1, col_index=1, text="", bbox=[500.0, 300.0, 900.0, 400.0]),
        ],
        bbox=[100.0, 100.0, 900.0, 400.0]
    )

    ocr_elements = [
        OCRElement(id="elem-1", text="Sanctioned Amount", bbox=[120.0, 120.0, 350.0, 160.0], confidence=0.98, page_number=1, line_number=1, source="ocr"),
        OCRElement(id="elem-2", text="94111.00", bbox=[520.0, 120.0, 700.0, 160.0], confidence=0.99, page_number=1, line_number=2, source="ocr"),
        OCRElement(id="elem-3", text="Interest Rate", bbox=[120.0, 320.0, 320.0, 360.0], confidence=0.97, page_number=1, line_number=3, source="ocr"),
        OCRElement(id="elem-4", text="21.84%", bbox=[520.0, 320.0, 650.0, 360.0], confidence=0.96, page_number=1, line_number=4, source="ocr"),
        # Element outside the table
        OCRElement(id="elem-out", text="Page 1 of 3", bbox=[400.0, 950.0, 600.0, 980.0], confidence=0.99, page_number=1, line_number=5, source="ocr"),
    ]

    consumed_ids = SpatialCellFusion.fuse_table_cells(
        table=table,
        ocr_elements=ocr_elements,
        page_width=1000.0,
        page_height=1000.0
    )

    assert consumed_ids == {"elem-1", "elem-2", "elem-3", "elem-4"}
    assert "elem-out" not in consumed_ids

    # Validate cell values
    assert table.cells[0].text == "Sanctioned Amount"
    assert table.cells[1].text == "94111.00"
    assert table.cells[2].text == "Interest Rate"
    assert table.cells[3].text == "21.84%"

    # Validate reconstructed rows
    assert table.rows_raw == [
        ["Sanctioned Amount", "94111.00"],
        ["Interest Rate", "21.84%"]
    ]
    assert table.headers == ["Sanctioned Amount", "94111.00"]


def test_spatial_cell_fusion_multi_token_reading_order():
    """
    Validates that multiple OCR tokens falling within the same cell are sorted
    correctly in top-to-bottom, left-to-right reading order.
    """
    table = TableStructure(
        id="tbl-reading-order",
        page_number=1,
        num_rows=1,
        num_cols=1,
        cells=[
            TableCell(row_index=0, col_index=0, text="", bbox=[100.0, 100.0, 500.0, 300.0])
        ],
        bbox=[100.0, 100.0, 500.0, 300.0]
    )

    # Line 1: "Annual Percentage Rate" (y: 110-140)
    # Line 2: "(APR) (%)" (y: 150-180)
    ocr_elements = [
        OCRElement(id="tok-2", text="(APR) (%)", bbox=[120.0, 150.0, 300.0, 180.0], confidence=0.95, page_number=1, line_number=2, source="ocr"),
        OCRElement(id="tok-1", text="Annual Percentage Rate", bbox=[120.0, 110.0, 420.0, 140.0], confidence=0.98, page_number=1, line_number=1, source="ocr"),
    ]

    consumed = SpatialCellFusion.fuse_table_cells(
        table=table,
        ocr_elements=ocr_elements,
        page_width=1000.0,
        page_height=1000.0
    )

    assert consumed == {"tok-1", "tok-2"}
    assert table.cells[0].text == "Annual Percentage Rate (APR) (%)"


def test_spatial_cell_fusion_preserves_preexisting_cell_text():
    """
    Validates that if a cell already contains text (e.g. from native digital PDF),
    it is not overwritten by spatial fusion.
    """
    table = TableStructure(
        id="tbl-native",
        page_number=1,
        num_rows=1,
        num_cols=1,
        cells=[
            TableCell(row_index=0, col_index=0, text="Digital Native Value", bbox=[100.0, 100.0, 500.0, 200.0])
        ],
        bbox=[100.0, 100.0, 500.0, 200.0]
    )

    ocr_elements = [
        OCRElement(id="tok-ocr", text="OCR Fallback Noise", bbox=[110.0, 110.0, 400.0, 180.0], confidence=0.80, page_number=1, line_number=1, source="ocr")
    ]

    consumed = SpatialCellFusion.fuse_table_cells(
        table=table,
        ocr_elements=ocr_elements,
        page_width=1000.0,
        page_height=1000.0
    )

    assert len(consumed) == 0
    assert table.cells[0].text == "Digital Native Value"


def test_spatial_cell_fusion_edge_cases():
    """
    Edge Cases:
    - Empty OCR list returns empty set without error.
    - Table with no cells returns empty set without error.
    - Zero or invalid image dimensions handled safely.
    - Tokens outside cell boundaries are ignored.
    """
    table = TableStructure(
        id="tbl-edge",
        page_number=1,
        num_rows=0,
        num_cols=0,
        cells=[],
        bbox=[0.0, 0.0, 100.0, 100.0]
    )

    assert SpatialCellFusion.fuse_table_cells(table, [], 100.0, 100.0) == set()

    table.cells = [TableCell(row_index=0, col_index=0, text="", bbox=[10.0, 10.0, 50.0, 50.0])]
    assert SpatialCellFusion.fuse_table_cells(table, [], 100.0, 100.0) == set()

    # Outside token
    outside_tok = [OCRElement(id="tok-out", text="Outside", bbox=[80.0, 80.0, 95.0, 95.0], confidence=0.9, page_number=1, line_number=1, source="ocr")]
    consumed = SpatialCellFusion.fuse_table_cells(table, outside_tok, 100.0, 100.0)
    assert len(consumed) == 0
    assert table.cells[0].text == ""


def test_end_to_end_serializer_recovers_tabular_data():
    """
    End-to-End: Verifies that DocumentSerializer outputs [TABLE] blocks with reconstructed
    tabular data when Docling provides empty cells and RapidOCR provides tokens.
    """
    serializer = DocumentSerializer()

    docling_res = DoclingParseResult(
        elements=[],
        tables=[
            TableStructure(
                id="tbl-kfs-p1",
                page_number=1,
                num_rows=2,
                num_cols=2,
                cells=[
                    TableCell(row_index=0, col_index=0, text="", bbox=[50.0, 100.0, 250.0, 200.0]),
                    TableCell(row_index=0, col_index=1, text="", bbox=[260.0, 100.0, 500.0, 200.0]),
                    TableCell(row_index=1, col_index=0, text="", bbox=[50.0, 210.0, 250.0, 310.0]),
                    TableCell(row_index=1, col_index=1, text="", bbox=[260.0, 210.0, 500.0, 310.0]),
                ],
                bbox=[50.0, 100.0, 500.0, 310.0]
            )
        ],
        page_count=1,
        pages_dimensions=[{"width": 600.0, "height": 800.0}]
    )

    ocr_elements = [
        OCRElement(id="hdr-1", text="Key Fact Sheet", bbox=[50.0, 20.0, 250.0, 50.0], confidence=0.99, page_number=1, line_number=1, source="ocr"),
        OCRElement(id="c-0-0", text="Loan proposal/ account No.", bbox=[60.0, 110.0, 240.0, 150.0], confidence=0.98, page_number=1, line_number=2, source="ocr"),
        OCRElement(id="c-0-1", text="2638871426000308_1", bbox=[270.0, 110.0, 480.0, 150.0], confidence=0.99, page_number=1, line_number=3, source="ocr"),
        OCRElement(id="c-1-0", text="Sanctioned Loan amount (in Rupees)", bbox=[60.0, 220.0, 245.0, 260.0], confidence=0.97, page_number=1, line_number=4, source="ocr"),
        OCRElement(id="c-1-1", text="94111.00", bbox=[270.0, 220.0, 380.0, 260.0], confidence=0.99, page_number=1, line_number=5, source="ocr"),
        OCRElement(id="ftr-1", text="Registered Office: Ahmedabad", bbox=[50.0, 750.0, 350.0, 780.0], confidence=0.95, page_number=1, line_number=6, source="ocr"),
    ]
    ocr_res = OCRResult(page_number=1, elements=ocr_elements, image_width=600.0, image_height=800.0)

    parsed_doc = serializer.build_unified_document(
        doc_id="DOC-KFS-TEST",
        filename="kfs.pdf",
        mime_type="application/pdf",
        file_size_bytes=4096,
        page_count=1,
        docling_result=docling_res,
        ocr_results=[ocr_res],
        vlm_corrections={},
        metrics=ProcessingMetrics()
    )

    # 1. Tables list has populated table
    assert len(parsed_doc.tables) == 1
    tbl = parsed_doc.tables[0]
    assert tbl.cells[0].text == "Loan proposal/ account No."
    assert tbl.cells[1].text == "2638871426000308_1"
    assert tbl.cells[2].text == "Sanctioned Loan amount (in Rupees)"
    assert tbl.cells[3].text == "94111.00"

    # 2. Reconstructed rows_raw contains table data
    assert tbl.rows_raw == [
        ["Loan proposal/ account No.", "2638871426000308_1"],
        ["Sanctioned Loan amount (in Rupees)", "94111.00"]
    ]

    # 3. Serialized text contains [TABLE] block AND header/footer text
    assert "Key Fact Sheet" in parsed_doc.text
    assert "[TABLE]" in parsed_doc.text
    assert "2638871426000308_1" in parsed_doc.text
    assert "94111.00" in parsed_doc.text
    assert "[/TABLE]" in parsed_doc.text
    assert "Registered Office: Ahmedabad" in parsed_doc.text


def test_empty_table_does_not_suppress_ocr_tokens():
    """
    Fallback safety: If Docling detects a table region but cell extraction fails completely
    and leaves the table with no content, the RapidOCR tokens inside that region MUST NOT
    be dropped into a black hole; they should be retained as layout elements.
    """
    serializer = DocumentSerializer()

    # Table with no cells and no text
    empty_docling = DoclingParseResult(
        elements=[],
        tables=[
            TableStructure(
                id="tbl-ghost",
                page_number=1,
                num_rows=0,
                num_cols=0,
                cells=[],
                bbox=[50.0, 100.0, 500.0, 300.0]
            )
        ],
        page_count=1,
        pages_dimensions=[{"width": 600.0, "height": 800.0}]
    )

    ocr_elements = [
        OCRElement(id="ghost-elem-1", text="Emergency Fallback Text", bbox=[60.0, 120.0, 300.0, 160.0], confidence=0.95, page_number=1, line_number=1, source="ocr")
    ]
    ocr_res = OCRResult(page_number=1, elements=ocr_elements, image_width=600.0, image_height=800.0)

    parsed_doc = serializer.build_unified_document(
        doc_id="DOC-FALLBACK-TEST",
        filename="fallback.pdf",
        mime_type="application/pdf",
        file_size_bytes=2048,
        page_count=1,
        docling_result=empty_docling,
        ocr_results=[ocr_res],
        vlm_corrections={},
        metrics=ProcessingMetrics()
    )

    # Text must NOT be lost
    assert "Emergency Fallback Text" in parsed_doc.text
    surviving_elems = [e for e in parsed_doc.elements if "Emergency Fallback Text" in e.text]
    assert len(surviving_elems) == 1


def test_ocr_table_detector_reconstructs_tables_when_docling_finds_none():
    """
    Validates that when Docling detects NO tables at all (e.g. docling_result=None or tables=[]),
    OCRTableDetector clusters spatial OCR elements into 2D rows and automatically emits
    structured [TABLE] markdown blocks instead of dumping raw unaligned 1D lines.
    """
    serializer = DocumentSerializer()

    ocr_elements = [
        # Non-table header
        OCRElement(id="hdr-1", text="Key Fact Sheet", bbox=[50.0, 20.0, 250.0, 50.0], confidence=0.99, page_number=1, line_number=1, source="ocr"),
        
        # Table Row 1 (y: 100-140)
        OCRElement(id="r1-c1", text="Loan proposal/ account No.", bbox=[50.0, 100.0, 250.0, 140.0], confidence=0.98, page_number=1, line_number=2, source="ocr"),
        OCRElement(id="r1-c2", text="2638871426000308_1", bbox=[270.0, 100.0, 450.0, 140.0], confidence=0.99, page_number=1, line_number=3, source="ocr"),
        OCRElement(id="r1-c3", text="Type of Loan", bbox=[470.0, 100.0, 550.0, 140.0], confidence=0.97, page_number=1, line_number=4, source="ocr"),
        OCRElement(id="r1-c4", text="TW", bbox=[560.0, 100.0, 600.0, 140.0], confidence=0.99, page_number=1, line_number=5, source="ocr"),

        # Table Row 2 (y: 150-190)
        OCRElement(id="r2-c1", text="Sanctioned Loan amount (in Rupees)", bbox=[50.0, 150.0, 350.0, 190.0], confidence=0.98, page_number=1, line_number=6, source="ocr"),
        OCRElement(id="r2-c2", text="94111.00", bbox=[400.0, 150.0, 500.0, 190.0], confidence=0.99, page_number=1, line_number=7, source="ocr"),

        # Table Row 3 (y: 200-240)
        OCRElement(id="r3-c1", text="Loan term (year/months/days)", bbox=[50.0, 200.0, 300.0, 240.0], confidence=0.98, page_number=1, line_number=8, source="ocr"),
        OCRElement(id="r3-c2", text="36 Months", bbox=[400.0, 200.0, 500.0, 240.0], confidence=0.99, page_number=1, line_number=9, source="ocr"),

        # Non-table footer
        OCRElement(id="ftr-1", text="Registered Office: Ahmedabad", bbox=[50.0, 750.0, 350.0, 780.0], confidence=0.95, page_number=1, line_number=10, source="ocr"),
    ]
    ocr_res = OCRResult(page_number=1, elements=ocr_elements, image_width=800.0, image_height=1000.0)

    parsed_doc = serializer.build_unified_document(
        doc_id="DOC-OCR-TABLE-TEST",
        filename="kfs_scanned.pdf",
        mime_type="application/pdf",
        file_size_bytes=4096,
        page_count=1,
        docling_result=None,
        ocr_results=[ocr_res],
        vlm_corrections={},
        metrics=ProcessingMetrics()
    )

    # [TABLE] block must be formed!
    assert "[TABLE]" in parsed_doc.text
    assert "[/TABLE]" in parsed_doc.text
    assert "2638871426000308_1" in parsed_doc.text
    assert "94111.00" in parsed_doc.text
    assert "36 Months" in parsed_doc.text
    assert "Key Fact Sheet" in parsed_doc.text
    assert "Registered Office: Ahmedabad" in parsed_doc.text
