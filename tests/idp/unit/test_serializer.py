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
    docling_elem1 = LayoutElement(
        id="elem-1",
        type=ElementType.HEADING,
        text="Experiment No. - 08",
        bbox=[12.0, 12.0, 190.0, 48.0],
        confidence=0.98,
        page_number=1,
        source="DOCLING",
        structure_source="docling"
    )
    docling_elem2 = LayoutElement(
        id="elem-4",
        type=ElementType.TEXT,
        text="Handwritten Note: Approved",
        bbox=[300.0, 500.0, 500.0, 530.0],
        confidence=0.90,
        page_number=1,
        source="DOCLING",
        structure_source="docling"
    )

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
        elements=[docling_elem1, docling_elem2],
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

    # 1. Non-table heading element comes from Docling
    heading_elements = [e for e in parsed_doc.elements if "Experiment" in e.text]
    assert len(heading_elements) == 1
    assert heading_elements[0].text == "Experiment No. - 08"
    assert heading_elements[0].source == "DOCLING"

    # 2. Table cell text comes from Docling Table Authority + VLM correction
    assert len(parsed_doc.tables) == 1
    tbl = parsed_doc.tables[0]
    assert tbl.cells[0].text == "Applicant Name"
    assert tbl.cells[1].text == "Rahul Sharma"  # VLM corrected in-place
    assert tbl.rows_raw == [["Applicant Name", "Rahul Sharma"]]

    # 3. Standalone element must be preserved
    standalone = [e for e in parsed_doc.elements if "Approved" in e.text]
    assert len(standalone) == 1
    assert standalone[0].text == "Handwritten Note: Approved"

    # 4. RapidOCR table text (ocr_cell0, ocr_cell1) MUST BE SKIPPED from elements list (region ownership)
    elements_text = [e.text for e in parsed_doc.elements]
    assert "Applicant Name" not in elements_text
    assert "Rahul Sharma" not in elements_text


def test_serializer_ocr_duplicate_safety():
    from idp.services.docling.parser import DoclingParseResult
    from idp.models.layout import LayoutElement, ElementType
    from idp.models.ocr import OCRResult, OCRElement
    from idp.models.processing import ProcessingMetrics

    serializer = DocumentSerializer()

    docling_elem1 = LayoutElement(
        id="elem-1",
        type=ElementType.HEADING,
        text="Duplicate Heading",
        bbox=[10.0, 10.0, 200.0, 50.0],
        confidence=0.95,
        page_number=1,
        source="DOCLING",
        structure_source="docling"
    )
    docling_elem2 = LayoutElement(
        id="elem-2",
        type=ElementType.HEADING,
        text="Duplicate Heading",
        bbox=[10.0, 10.0, 200.0, 50.0],
        confidence=0.95,
        page_number=1,
        source="DOCLING",
        structure_source="docling"
    )

    docling_res = DoclingParseResult(
        elements=[docling_elem1, docling_elem2],
        tables=[],
        page_count=1,
        pages_dimensions=[{"width": 595.0, "height": 842.0}]
    )

    metrics = ProcessingMetrics()

    parsed_doc = serializer.build_unified_document(
        doc_id="TEST-DEDUP",
        filename="test.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        page_count=1,
        docling_result=docling_res,
        ocr_results=[],
        vlm_corrections={},
        metrics=metrics
    )

    # Should only contain 1 instance of the duplicate heading
    assert len(parsed_doc.elements) == 1
    assert parsed_doc.elements[0].text == "Duplicate Heading"


def test_serializer_docling_primary_authority_over_rapidocr():
    """Verify that Docling layout text elements take priority over RapidOCR elements."""
    from idp.services.docling.parser import DoclingParseResult
    from idp.models.layout import LayoutElement, ElementType
    from idp.models.ocr import OCRResult, OCRElement
    from idp.models.processing import ProcessingMetrics
    from idp.services.vlm.client import VLMResult

    serializer = DocumentSerializer()

    docling_elem1 = LayoutElement(
        id="elem-1",
        type=ElementType.HEADING,
        text="SANCTION LETTER",
        bbox=[10.0, 10.0, 300.0, 50.0],
        confidence=0.98,
        page_number=1,
        source="DOCLING",
        structure_source="docling"
    )
    docling_elem2 = LayoutElement(
        id="elem-2",
        type=ElementType.TEXT,
        text="Loan amount approved: INR 500,000",
        bbox=[10.0, 60.0, 400.0, 90.0],
        confidence=0.65,
        page_number=1,
        source="DOCLING",
        structure_source="docling"
    )

    docling_res = DoclingParseResult(
        elements=[docling_elem1, docling_elem2],
        tables=[],
        page_count=1,
        pages_dimensions=[{"width": 595.0, "height": 842.0}]
    )

    ocr_elem = OCRElement(
        id="ocr-1",
        text="RAPIDOCR GARBLED TEXT",
        bbox=[10.0, 100.0, 400.0, 130.0],
        confidence=0.50,
        page_number=1,
        line_number=1,
        source="ocr"
    )
    ocr_res = OCRResult(page_number=1, elements=[ocr_elem])

    # VLM correction for elem-2
    vlm_corrections = {
        "elem-2": VLMResult(text="Loan amount approved: INR 500,000 (Corrected)", confidence=0.99, verified=True)
    }

    metrics = ProcessingMetrics()

    parsed_doc = serializer.build_unified_document(
        doc_id="TEST-DOCLING-PRIMARY",
        filename="sanction.pdf",
        mime_type="application/pdf",
        file_size_bytes=2048,
        page_count=1,
        docling_result=docling_res,
        ocr_results=[ocr_res],
        vlm_corrections=vlm_corrections,
        metrics=metrics,
        docling_used=True
    )

    # 1. Elements come from Docling
    assert len(parsed_doc.elements) == 2
    assert parsed_doc.elements[0].text == "SANCTION LETTER"
    assert parsed_doc.elements[0].source == "DOCLING"
    assert parsed_doc.elements[0].structure_source == "docling"

    # 2. elem-2 has VLM correction applied
    assert parsed_doc.elements[1].text == "Loan amount approved: INR 500,000 (Corrected)"
    assert parsed_doc.elements[1].source == "vlm_corrected"
    assert parsed_doc.elements[1].confidence == 0.99

    # 3. RapidOCR fallback elements are NOT ingested because Docling elements were present
    assert not any("RAPIDOCR" in e.text for e in parsed_doc.elements)
    assert "SANCTION LETTER" in parsed_doc.text
    assert "INR 500,000 (Corrected)" in parsed_doc.text


_GLYPH = "×"  # a corrupted glyph that trips is_garbled_text() but survives text cleanup


def test_has_recoverable_value_signals():
    """Rows carrying a real structured value are recoverable; pure garble is not."""
    rv = DocumentSerializer._has_recoverable_value

    # Structured / numeric values that must survive a garbled row
    assert rv("Application Date:06082026 ApplINo.:AP" + _GLYPH + "L00343265") is True  # >=5 digit run
    assert rv("GSTINNo.08AO0" + _GLYPH + "K6924P1Z2") is True                          # mixed letter+digit token
    assert rv("Account Number 50200064998229") is True
    assert rv("Email KHATRIPRAKASH79@GMAIL.COM") is True

    # No recoverable value -> safe to drop
    assert rv("HRAHRR INCOMETAXDEPARTMENT") is False   # pure-letter run, no digits
    assert rv("3TET RRTO") is False
    assert rv("") is False


def test_serializer_retains_partial_garble_row_with_value():
    """A garbled Docling row that still holds a structured value is kept (flagged),
    not silently dropped; a garbled row with no value is still dropped."""
    from idp.services.docling.parser import DoclingParseResult
    from idp.models.layout import LayoutElement, ElementType
    from idp.models.processing import ProcessingMetrics

    serializer = DocumentSerializer()

    valuable = LayoutElement(
        id="row-appl-no",
        type=ElementType.TEXT,
        # corrupted glyph fused with a real application number -> is_garbled_text() == True
        text="Application Date:06082026 ApplINo.:AP" + _GLYPH + "L00343265",
        bbox=[10.0, 10.0, 400.0, 30.0],
        confidence=0.9,
        page_number=1,
        source="docling_ocr",
        structure_source="docling",
    )
    pure_noise = LayoutElement(
        id="row-noise",
        type=ElementType.TEXT,
        text="HRTRR RROR HRAR",
        bbox=[10.0, 40.0, 400.0, 60.0],
        confidence=0.9,
        page_number=1,
        source="docling_ocr",
        structure_source="docling",
    )

    docling_res = DoclingParseResult(
        elements=[valuable, pure_noise],
        tables=[],
        page_count=1,
        pages_dimensions=[{"width": 595.0, "height": 842.0}],
    )

    parsed_doc = serializer.build_unified_document(
        doc_id="TEST-PARTIAL-GARBLE",
        filename="form.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        page_count=1,
        docling_result=docling_res,
        ocr_results=[],
        vlm_corrections={},
        metrics=ProcessingMetrics(),
    )

    texts = [e.text for e in parsed_doc.elements]
    assert any("00343265" in t for t in texts), "value-bearing garbled row was dropped"
    assert not any("HRTRR" in t for t in texts), "pure-noise row should still be dropped"

    kept = next(e for e in parsed_doc.elements if "00343265" in e.text)
    assert kept.metadata.get("partial_garble_retained") is True
    assert kept.metadata.get("needs_vlm") is True
    assert kept.confidence <= 0.35


def test_comb_box_merged_tokens_stay_near_their_field_label():
    """Regression: merged comb-box tokens used to get a hardcoded
    reading_order=9999, so EVERY comb-box field's value on a page (e.g. both
    "Applicant Name" and "Father's Name", each split into one-character-per-
    box cells) collapsed into one undifferentiated block at the very end of
    the page's text, after every other label -- starving the downstream LLM
    field extractor of the label-proximity context it needs to tell the two
    fields apart. The merged "PRAKASH"/"KHATRI" tokens must now appear in
    full_text BEFORE the unrelated "Father:" label two rows below, not after
    it."""
    from idp.services.docling.parser import DoclingParseResult
    from idp.models.layout import LayoutElement, ElementType
    from idp.models.processing import ProcessingMetrics

    serializer = DocumentSerializer()

    elements = []
    order = 1

    # Row 1: "Name:" label followed by a comb-box "PRAKASH" + "KHATRI" (one
    # character per printed cell, as Docling/OCR would emit it).
    elements.append(LayoutElement(
        id="lbl-name", type=ElementType.TEXT, text="Name:",
        bbox=[50.0, 100.0, 90.0, 112.0], confidence=0.95,
        page_number=1, source="docling_ocr", structure_source="docling",
        reading_order=order,
    ))
    order += 1
    x = 100.0
    for ch in "PRAKASH":
        elements.append(LayoutElement(
            id=f"name-{ch}-{order}", type=ElementType.TEXT, text=ch,
            bbox=[x, 100.0, x + 10.0, 114.0], confidence=0.9,
            page_number=1, source="docling_ocr", structure_source="docling",
            reading_order=order,
        ))
        order += 1
        x += 15.0
    x += 15.0  # word gap before surname
    for ch in "KHATRI":
        elements.append(LayoutElement(
            id=f"surname-{ch}-{order}", type=ElementType.TEXT, text=ch,
            bbox=[x, 100.0, x + 10.0, 114.0], confidence=0.9,
            page_number=1, source="docling_ocr", structure_source="docling",
            reading_order=order,
        ))
        order += 1
        x += 15.0

    # Row 2 (visually lower on the page, later reading order): "Father:"
    # label followed by its own comb-box name -- unrelated to row 1.
    elements.append(LayoutElement(
        id="lbl-father", type=ElementType.TEXT, text="Father:",
        bbox=[50.0, 150.0, 90.0, 162.0], confidence=0.95,
        page_number=1, source="docling_ocr", structure_source="docling",
        reading_order=order,
    ))
    order += 1
    x = 100.0
    for ch in "GYAN":
        elements.append(LayoutElement(
            id=f"father-{ch}-{order}", type=ElementType.TEXT, text=ch,
            bbox=[x, 150.0, x + 10.0, 164.0], confidence=0.9,
            page_number=1, source="docling_ocr", structure_source="docling",
            reading_order=order,
        ))
        order += 1
        x += 15.0

    docling_res = DoclingParseResult(
        elements=elements,
        tables=[],
        page_count=1,
        pages_dimensions=[{"width": 595.0, "height": 842.0}],
    )

    parsed_doc = serializer.build_unified_document(
        doc_id="TEST-COMB-ORDER",
        filename="kyc_form.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        page_count=1,
        docling_result=docling_res,
        ocr_results=[],
        vlm_corrections={},
        metrics=ProcessingMetrics(),
    )

    merged_texts = {e.text for e in parsed_doc.elements if e.source == "comb_box_merged"}
    assert "PRAKASH" in merged_texts
    assert "KHATRI" in merged_texts

    name_idx = parsed_doc.text.index("PRAKASH")
    # full_text passes labels through clean_ocr_text, which strips the
    # trailing colon ("Father:" -> "Father") -- search for the stripped form.
    father_label_idx = parsed_doc.text.index("Father")
    assert name_idx < father_label_idx, (
        "merged 'PRAKASH' token landed after the unrelated 'Father:' label -- "
        "comb-box merged tokens are not staying near their own field label"
    )
