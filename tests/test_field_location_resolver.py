"""Unit and integration tests for FieldLocationResolver."""
import pytest
from typing import Dict, Any, List

from idp.services.extraction.field_location_resolver import (
    FieldLocationResolver,
    _ensure_normalized_bbox,
    _clean_numeric,
    _normalize_date,
)
from idp.models.extraction import FieldLocation, OCRTokenDebug


@pytest.fixture
def resolver() -> FieldLocationResolver:
    return FieldLocationResolver()


class TestFieldLocationResolver:
    """Comprehensive test suite for OCR Token to Field Location mapping."""

    def test_exact_match_single_token(self, resolver: FieldLocationResolver):
        """Validates exact case-insensitive match for single-word tokens."""
        extracted = {
            "pan_number": "ABCDE1234F"
        }
        ocr_elements = [
            {
                "id": "tok-1",
                "text": "Permanent Account Number",
                "bbox": [0.1, 0.1, 0.5, 0.15],
                "page_number": 1,
                "confidence": 0.98,
            },
            {
                "id": "tok-2",
                "text": "abcde1234f",  # lower-case in OCR
                "bbox": [0.1, 0.2, 0.35, 0.25],
                "page_number": 1,
                "confidence": 0.95,
            }
        ]

        result = resolver.resolve_fields(extracted, ocr_elements)
        assert "pan_number" in result
        loc = result["pan_number"]
        assert loc.location_status == "resolved"
        assert loc.page == 1
        assert loc.bbox == [0.1, 0.2, 0.35, 0.25]
        assert loc.matched_text == "abcde1234f"
        assert loc.match_strategy in ("exact_match", "alphanumeric_match")
        assert loc.match_confidence >= 0.95

    def test_multi_token_sliding_window(self, resolver: FieldLocationResolver):
        """Validates that consecutive tokens on the same line/row merge their bounding boxes."""
        extracted = {
            "applicant_name": "Anvay Uparkar"
        }
        ocr_elements = [
            {
                "id": "tok-1",
                "text": "Name:",
                "bbox": [0.05, 0.1, 0.15, 0.14],
                "page_number": 1,
                "confidence": 0.99,
            },
            {
                "id": "tok-2",
                "text": "Anvay",
                "bbox": [0.20, 0.1, 0.35, 0.14],
                "page_number": 1,
                "confidence": 0.94,
            },
            {
                "id": "tok-3",
                "text": "Uparkar",
                "bbox": [0.36, 0.1, 0.55, 0.14],
                "page_number": 1,
                "confidence": 0.92,
            }
        ]

        result = resolver.resolve_fields(extracted, ocr_elements)
        assert "applicant_name" in result
        loc = result["applicant_name"]
        assert loc.location_status == "resolved"
        assert loc.page == 1
        # BBox must enclose both tokens: x1=0.20, y1=0.1, x2=0.55, y2=0.14
        assert loc.bbox[0] == pytest.approx(0.20, abs=1e-3)
        assert loc.bbox[1] == pytest.approx(0.10, abs=1e-3)
        assert loc.bbox[2] == pytest.approx(0.55, abs=1e-3)
        assert loc.bbox[3] == pytest.approx(0.14, abs=1e-3)
        assert loc.matched_text == "Anvay Uparkar"
        assert "multi_token" in loc.match_strategy
        assert loc.match_confidence >= 0.90

    def test_numeric_and_currency_normalization(self, resolver: FieldLocationResolver):
        """Validates that currency symbols and commas are normalized for numeric fields."""
        extracted = {
            "loan_amount": "94111"
        }
        ocr_elements = [
            {
                "id": "tok-1",
                "text": "Sanctioned Amount",
                "bbox": [0.1, 0.3, 0.4, 0.35],
                "page_number": 1,
                "confidence": 0.98,
            },
            {
                "id": "tok-2",
                "text": "₹ 94,111.00",
                "bbox": [0.45, 0.3, 0.65, 0.35],
                "page_number": 1,
                "confidence": 0.96,
            }
        ]

        result = resolver.resolve_fields(extracted, ocr_elements)
        assert "loan_amount" in result
        loc = result["loan_amount"]
        assert loc.location_status == "resolved"
        assert loc.page == 1
        assert loc.bbox == [0.45, 0.3, 0.65, 0.35]
        assert loc.matched_text == "₹ 94,111.00"
        assert "numeric" in loc.match_strategy

    def test_date_normalization(self, resolver: FieldLocationResolver):
        """Validates date matching across formats (e.g. 28/08/2026 vs 28-08-2026)."""
        extracted = {
            "application_date": "28/08/2026"
        }
        ocr_elements = [
            {
                "id": "tok-date",
                "text": "28-08-2026",
                "bbox": [0.2, 0.5, 0.4, 0.55],
                "page_number": 1,
                "confidence": 0.97,
            }
        ]

        result = resolver.resolve_fields(extracted, ocr_elements)
        assert "application_date" in result
        loc = result["application_date"]
        assert loc.location_status == "resolved"
        assert loc.bbox == [0.2, 0.5, 0.4, 0.55]
        assert loc.matched_text == "28-08-2026"

    def test_table_cell_matching(self, resolver: FieldLocationResolver):
        """Validates that Docling table cells are included in spatial resolution."""
        extracted = {
            "emi": "3586"
        }
        ocr_elements = [
            {
                "id": "tok-heading",
                "text": "Key Fact Sheet",
                "bbox": [0.1, 0.05, 0.4, 0.09],
                "page_number": 1,
                "confidence": 0.99,
            }
        ]
        table_cells = [
            {
                "id": "cell-1",
                "text": "3586",
                "bbox": [0.6, 0.4, 0.75, 0.45],
                "page_number": 1,
                "confidence": 0.95,
            }
        ]

        result = resolver.resolve_fields(extracted, ocr_elements, table_cells=table_cells)
        assert "emi" in result
        loc = result["emi"]
        assert loc.location_status == "resolved"
        assert loc.bbox == [0.6, 0.4, 0.75, 0.45]
        assert loc.matched_text == "3586"

    def test_fuzzy_matching_above_threshold(self, resolver: FieldLocationResolver):
        """Validates that minor OCR typos above 0.82 similarity threshold resolve properly."""
        extracted = {
            "applicant_name": "MUKESH SHANKAR"
        }
        ocr_elements = [
            {
                "id": "tok-name",
                "text": "MUKESH SHANKR",  # OCR captured single letter typo
                "bbox": [0.2, 0.2, 0.6, 0.25],
                "page_number": 1,
                "confidence": 0.96,
            }
        ]

        result = resolver.resolve_fields(extracted, ocr_elements)
        assert "applicant_name" in result
        loc = result["applicant_name"]
        assert loc.location_status == "resolved"
        assert loc.matched_text == "MUKESH SHANKR"
        assert loc.match_confidence >= 0.82

    def test_fuzzy_matching_below_threshold_rejected(self, resolver: FieldLocationResolver):
        """Validates that dissimilar text is not falsely matched and marked unresolved."""
        extracted = {
            "applicant_name": "ANVAY UPARKAR"
        }
        ocr_elements = [
            {
                "id": "tok-wrong",
                "text": "RAJESH SHARMA",
                "bbox": [0.1, 0.1, 0.4, 0.15],
                "page_number": 1,
                "confidence": 0.99,
            }
        ]

        result = resolver.resolve_fields(extracted, ocr_elements)
        assert "applicant_name" in result
        loc = result["applicant_name"]
        assert loc.location_status == "unresolved"
        assert loc.bbox is None
        assert "no sufficiently confident" in loc.reason.lower()

    def test_unresolved_field_no_hallucination(self, resolver: FieldLocationResolver):
        """Validates that null, missing, or unavailable values produce unresolved locations with zero hallucinated coordinates."""
        extracted = {
            "bank_account_no": None,
            "pan_number": "XYZPP9999Q"
        }
        ocr_elements = [
            {
                "id": "tok-1",
                "text": "Application Form",
                "bbox": [0.1, 0.1, 0.5, 0.15],
                "page_number": 1,
                "confidence": 0.99,
            }
        ]

        result = resolver.resolve_fields(extracted, ocr_elements)
        # null fields are skipped
        assert "bank_account_no" not in result

        assert "pan_number" in result
        pan_loc = result["pan_number"]
        assert pan_loc.location_status == "unresolved"
        assert pan_loc.bbox is None
        assert pan_loc.matched_text is None
        assert pan_loc.reason is not None

    def test_multi_page_disambiguation(self, resolver: FieldLocationResolver):
        """Validates that tokens on page 2 are matched with the correct page number."""
        extracted = {
            "borrower_address": "Navpada Vile Parle East"
        }
        ocr_elements = [
            {
                "id": "tok-p1",
                "text": "Header Page 1",
                "bbox": [0.1, 0.05, 0.4, 0.1],
                "page_number": 1,
                "confidence": 0.99,
            },
            {
                "id": "tok-p2-1",
                "text": "Navpada",
                "bbox": [0.1, 0.3, 0.25, 0.34],
                "page_number": 2,
                "confidence": 0.95,
            },
            {
                "id": "tok-p2-2",
                "text": "Vile",
                "bbox": [0.26, 0.3, 0.35, 0.34],
                "page_number": 2,
                "confidence": 0.95,
            },
            {
                "id": "tok-p2-3",
                "text": "Parle",
                "bbox": [0.36, 0.3, 0.45, 0.34],
                "page_number": 2,
                "confidence": 0.95,
            },
            {
                "id": "tok-p2-4",
                "text": "East",
                "bbox": [0.46, 0.3, 0.55, 0.34],
                "page_number": 2,
                "confidence": 0.95,
            }
        ]

        result = resolver.resolve_fields(extracted, ocr_elements)
        assert "borrower_address" in result
        loc = result["borrower_address"]
        assert loc.location_status == "resolved"
        assert loc.page == 2
        assert loc.bbox[0] == pytest.approx(0.1, abs=1e-3)
        assert loc.bbox[2] == pytest.approx(0.55, abs=1e-3)

    def test_get_debug_tokens(self, resolver: FieldLocationResolver):
        """Validates that raw OCR elements are correctly converted to OCRTokenDebug models."""
        ocr_elements = [
            {
                "id": "tok-1",
                "text": "Sanctioned",
                "bbox": [100, 200, 300, 250],
                "page_number": 1,
                "confidence": 0.96,
                "block_id": 1,
                "line_id": 2,
            }
        ]
        page_dims = [{"width": 1000, "height": 1000}]

        tokens = resolver.get_debug_tokens(ocr_elements, page_dims)
        assert len(tokens) == 1
        tok = tokens[0]
        assert isinstance(tok, OCRTokenDebug)
        assert tok.text == "Sanctioned"
        assert tok.bbox == [0.1, 0.2, 0.3, 0.25]
        assert tok.bbox_pixels == [100.0, 200.0, 300.0, 250.0]
        assert tok.confidence == 0.96


class TestCoordinateNormalization:
    """Tests coordinate conversion and normalization utilities."""

    def test_ensure_normalized_bbox_with_pixel_dims(self):
        # 1000 x 2000 page
        raw_bbox = [100, 200, 500, 600]
        norm, pix = _ensure_normalized_bbox(raw_bbox, page_w=1000, page_h=2000)
        assert norm == [0.1, 0.1, 0.5, 0.3]
        assert pix == [100.0, 200.0, 500.0, 600.0]

    def test_ensure_normalized_bbox_already_normalized(self):
        raw_bbox = [0.12, 0.34, 0.56, 0.78]
        norm, pix = _ensure_normalized_bbox(raw_bbox)
        assert norm == [0.12, 0.34, 0.56, 0.78]

    def test_clean_numeric(self):
        assert _clean_numeric("₹ 94,111.00") == "94111"
        assert _clean_numeric("Rs. 3586") == "3586"
        assert _clean_numeric("12.5%") == "12.5"

    def test_normalize_date(self):
        assert _normalize_date("28/08/2026") == "28082026"
        assert _normalize_date("28-08-2026") == "28082026"
        assert _normalize_date("28.08.2026") == "28082026"
