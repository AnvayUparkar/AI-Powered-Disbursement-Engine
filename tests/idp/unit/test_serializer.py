import os
import tempfile
from idp.services.output.serializer import DocumentSerializer


def test_xml_fast_path_serializer():
    serializer = DocumentSerializer()
    xml_content = """<?xml version="1.0"?>
    <application>
        <applicant_name>Rahul Sharma</applicant_name>
        <amount>500000</amount>
    </application>
    """
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False, mode="w") as f:
        f.write(xml_content)
        tmp_name = f.name

    try:
        parsed_doc = serializer.parse_xml_fast_path(tmp_name, doc_id="TEST-XML")
        assert parsed_doc.document_id == "TEST-XML"
        assert parsed_doc.processing.docling_used is False
        assert "Rahul Sharma" in parsed_doc.text
        assert len(parsed_doc.elements) == 2
    finally:
        os.remove(tmp_name)


def test_serializer_region_alignment_and_docling_text_ignored():
    from idp.services.docling.parser import DoclingParseResult
    from idp.models.layout import LayoutElement, ElementType
    from idp.models.table import TableStructure, TableCell
    from idp.models.ocr import OCRResult, OCRElement
    from idp.models.processing import ProcessingMetrics
    from idp.services.vlm.client import VLMResult

    serializer = DocumentSerializer()

    # Docling structural elements
    # Docling table structure (1 row, 2 cols) with cell texts
    docling_table = TableStructure(
        id="table-1",
        page_number=1,
        num_rows=1,
        num_cols=2,
        cells=[
            TableCell(row_index=0, col_index=0, text="Applicant Name", bbox=[10.0, 100.0, 100.0, 150.0]),
            TableCell(row_index=0, col_index=1, text="Rahul Sharrna", bbox=[100.0, 100.0, 200.0, 150.0]),
        ],
        bbox=[10.0, 100.0, 200.0, 150.0]
    )

    docling_res = DoclingParseResult(
        elements=[],
        tables=[docling_table],
        page_count=1,
        pages_dimensions=[{"width": 595.0, "height": 842.0}]
    )

    # RapidOCR elements (canonical text)
    ocr_heading = OCRElement(
        id="ocr-1",
        text="Experiment No. - 08",
        bbox=[12.0, 12.0, 190.0, 48.0],
        confidence=0.98,
        page_number=1,
        line_number=1,
        source="ocr"
    )

    ocr_cell0 = OCRElement(
        id="ocr-2",
        text="Applicant Name",
        bbox=[12.0, 105.0, 95.0, 145.0],
        confidence=0.95,
        page_number=1,
        line_number=2,
        source="ocr"
    )

    ocr_cell1 = OCRElement(
        id="ocr-3",
        text="Rahul Sharrna",
        bbox=[105.0, 105.0, 195.0, 145.0],
        confidence=0.60,
        page_number=1,
        line_number=3,
        source="ocr"
    )

    # Unmapped OCR text (standalone OCR)
    ocr_standalone = OCRElement(
        id="ocr-4",
        text="Handwritten Note: Approved",
        bbox=[300.0, 500.0, 500.0, 530.0],
        confidence=0.90,
        page_number=1,
        line_number=4,
        source="ocr"
    )

    ocr_res = OCRResult(page_number=1, elements=[ocr_heading, ocr_cell0, ocr_cell1, ocr_standalone])

    # VLM correction for cell
    vlm_corrections = {
        "cell-table-1-0-1": VLMResult(text="Rahul Sharma", confidence=0.99, verified=True)
    }

    metrics = ProcessingMetrics()

    parsed_doc = serializer.build_unified_document(
        doc_id="TEST-ALIGN",
        filename="test.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        page_count=1,
        docling_result=docling_res,
        ocr_results=[ocr_res],
        vlm_corrections=vlm_corrections,
        metrics=metrics
    )

    # 1. Non-table heading element comes from RapidOCR
    heading_elements = [e for e in parsed_doc.elements if "Experiment" in e.text]
    assert len(heading_elements) == 1
    assert heading_elements[0].text == "Experiment No. - 08"
    assert heading_elements[0].source == "RAPIDOCR"

    # 2. Table cell text comes from Docling Table Authority + VLM correction
    assert len(parsed_doc.tables) == 1
    tbl = parsed_doc.tables[0]
    assert tbl.cells[0].text == "Applicant Name"
    assert tbl.cells[1].text == "Rahul Sharma"  # VLM corrected in-place
    assert tbl.rows_raw == [["Applicant Name", "Rahul Sharma"]]

    # 3. Standalone OCR element must be preserved
    standalone = [e for e in parsed_doc.elements if "Approved" in e.text]
    assert len(standalone) == 1
    assert standalone[0].text == "Handwritten Note: Approved"
    assert standalone[0].source == "RAPIDOCR"

    # 4. RapidOCR table text (ocr_cell0, ocr_cell1) MUST BE SKIPPED from elements list (region ownership)
    elements_text = [e.text for e in parsed_doc.elements]
    assert "Applicant Name" not in elements_text
    assert "Rahul Sharma" not in elements_text


def test_serializer_ocr_duplicate_safety():
    from idp.services.docling.parser import DoclingParseResult
    from idp.models.ocr import OCRResult, OCRElement
    from idp.models.processing import ProcessingMetrics

    serializer = DocumentSerializer()

    docling_res = DoclingParseResult(
        elements=[],
        tables=[],
        page_count=1,
        pages_dimensions=[{"width": 595.0, "height": 842.0}]
    )

    # Two exact duplicate OCR lines
    ocr1 = OCRElement(
        id="ocr-1",
        text="Duplicate Heading",
        bbox=[10.0, 10.0, 200.0, 50.0],
        confidence=0.95,
        page_number=1,
        line_number=1,
        source="ocr"
    )
    ocr2 = OCRElement(
        id="ocr-2",
        text="Duplicate Heading",
        bbox=[10.0, 10.0, 200.0, 50.0],
        confidence=0.95,
        page_number=1,
        line_number=2,
        source="ocr"
    )

    ocr_res = OCRResult(page_number=1, elements=[ocr1, ocr2])
    metrics = ProcessingMetrics()

    parsed_doc = serializer.build_unified_document(
        doc_id="TEST-DEDUP",
        filename="test.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        page_count=1,
        docling_result=docling_res,
        ocr_results=[ocr_res],
        vlm_corrections={},
        metrics=metrics
    )

    # Should only contain 1 instance of the duplicate heading
    assert len(parsed_doc.elements) == 1
    assert parsed_doc.elements[0].text == "Duplicate Heading"

