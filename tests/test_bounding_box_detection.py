import pytest
from idp.services.ocr.confidence import OCRConfidenceEvaluator
from idp.services.fusion.region_mask import TableRegionMask
from idp.services.output.serializer import DocumentSerializer
from idp.services.extraction.field_location_resolver import FieldLocationResolver
from idp.models.table import TableRegion, TableStructure, TableCell
from idp.models.layout import LayoutElement, ElementType


def test_confidence_evaluator_preserves_numbers_and_dates():
    """Verify that clean_bilingual_label_noise and is_garbled_text preserve numbers and dates."""
    evaluator = OCRConfidenceEvaluator()

    # 1. Clean bilingual label noise on numeric and date inputs
    cleaned_date = evaluator.clean_bilingual_label_noise("15/05/1992")
    assert "15/05/1992" in cleaned_date or "15" in cleaned_date

    cleaned_num = evaluator.clean_bilingual_label_noise("500000")
    assert "500000" in cleaned_num

    # 2. Standalone digits should NOT be flagged as garbled text
    assert not evaluator.is_garbled_text("500000")
    assert not evaluator.is_garbled_text("2024")
    assert not evaluator.is_garbled_text("100")
    assert not evaluator.is_garbled_text("12")


def test_table_region_mask_cell_content_gating():
    """Verify TableRegionMask retains text inside table region if cell text was not captured."""
    table = TableStructure(
        id="tbl-1",
        page_number=1,
        num_rows=1,
        num_cols=1,
        bbox=[0.1, 0.1, 0.5, 0.5],
        cells=[TableCell(row_index=0, col_index=0, text="Captured Cell Content")]
    )
    region = TableRegion(page_number=1, bbox=[0.1, 0.1, 0.5, 0.5], table_id="tbl-1", table_data=table)
    table_regions = [region]

    # Case 1: Text element matches captured cell -> Should be blocked
    is_blocked, code = TableRegionMask.is_inside_or_overlapping_table(
        rapidocr_bbox=[0.15, 0.15, 0.35, 0.35],
        table_regions=table_regions,
        text="Captured Cell Content"
    )
    assert is_blocked
    assert code == "SKIPPED_INSIDE_DOCLING_TABLE"

    # Case 2: Text element was missed by table structure -> Should be retained
    is_blocked_missed, code_missed = TableRegionMask.is_inside_or_overlapping_table(
        rapidocr_bbox=[0.15, 0.15, 0.35, 0.35],
        table_regions=table_regions,
        text="Uncaptured Form Field Value"
    )
    assert not is_blocked_missed
    assert code_missed == "RETAINED_UNCAPTURED_TABLE_TEXT"


def test_serializer_deduplication_preserves_adjacent_stacked_fields():
    """Verify _is_duplicate keeps stacked key-value form fields even if bboxes overlap."""
    existing_elem = LayoutElement(
        id="elem-1",
        type=ElementType.TEXT,
        text="Applicant Name",
        bbox=[0.10, 0.10, 0.40, 0.15],
        confidence=0.95,
        page_number=1
    )
    existing = [existing_elem]

    # Candidate has different text ("RAKESH SHARMA") with slightly overlapping bbox
    candidate_bbox = [0.10, 0.12, 0.40, 0.18]
    is_dup = DocumentSerializer._is_duplicate(
        ocr_bbox=candidate_bbox,
        existing_elements=existing,
        iou_threshold=0.75,
        text="RAKESH SHARMA"
    )
    # Should NOT drop as duplicate because text is different
    assert not is_dup


def test_field_location_resolver_key_anchor_proximity():
    """Verify FieldLocationResolver uses Key-Anchor Proximity fallback when exact value match is absent."""
    resolver = FieldLocationResolver()

    # Input OCR elements contain field label "Applicant Name:" and value "RAKESH SHARMA" nearby
    ocr_elements = [
        {"id": "tok-1", "text": "Applicant Name:", "bbox": [100, 100, 200, 120], "page_number": 1, "confidence": 0.95},
        {"id": "tok-2", "text": "RAKESH SHARMA", "bbox": [210, 100, 350, 120], "page_number": 1, "confidence": 0.95},
    ]

    # User extracts field `applicant_name` with normalized value "Rakesh Sharma"
    extracted = {"applicant_name": "Rakesh Sharma"}

    res = resolver.resolve_field_locations(
        extracted_fields=extracted,
        ocr_elements=ocr_elements,
        page_dimensions=[{"width": 800, "height": 1100}]
    )

    assert "applicant_name" in res
    loc = res["applicant_name"]
    assert loc.location_status == "resolved"
    assert loc.bbox is not None
    assert len(loc.bbox) == 4
