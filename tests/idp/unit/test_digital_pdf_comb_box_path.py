"""
Tests for the digital-PDF comb-box detection path.

Root cause context:
  Docling's default PDF_AWARE_LAYOUT_REGIONS OCR mode skips re-OCR for any
  layout region that already carries native PDF text. On a digital comb-box
  form, that means the entire character-box field is excluded; CombBoxDetector
  never receives per-character tokens and the field never merges.

  fix: DIGITAL_PDF_PROFILE.force_full_page_ocr = True
  Switches Docling to OcrMode.FULL_PAGE, re-OCRing every region regardless
  of native text so digital comb-box grids produce per-character tokens that
  CombBoxDetector can merge identically to how it handles scanned documents.
"""

import pytest
from config.docling_profiles import (
    CHARACTER_BOX_FORMS_PROFILE,
    DIGITAL_PDF_PROFILE,
    SCANNED_DOCUMENTS_PROFILE,
    MIXED_CONTENT_PROFILE,
    DOCLING_PROFILES,
)
from idp.services.extraction.comb_box_detector import CombBoxDetector
from idp.models.layout import LayoutElement, ElementType


def _make_char_elements(
    chars: str,
    x0: float = 100.0,
    y0: float = 200.0,
    box_w: float = 18.0,
    box_h: float = 20.0,
    gap: float = 2.0,
    page_number: int = 1,
) -> list:
    """Return one LayoutElement per character, evenly spaced in a row."""
    pitch = box_w + gap
    return [
        LayoutElement(
            id=f"char-{i}",
            type=ElementType.TEXT,
            text=ch,
            bbox=[x0 + i * pitch, y0, x0 + i * pitch + box_w, y0 + box_h],
            confidence=0.95,
            page_number=page_number,
            reading_order=i,
            source="docling_ocr",
            structure_source="docling",
        )
        for i, ch in enumerate(chars)
    ]


class TestDigitalPdfProfileGuards:
    def test_digital_pdf_profile_forces_full_page_ocr(self):
        """DIGITAL_PDF_PROFILE must use FULL_PAGE OCR so comb-box fields on
        digital documents produce per-character tokens for CombBoxDetector."""
        assert DIGITAL_PDF_PROFILE.force_full_page_ocr is True

    def test_character_box_forms_profile_forces_full_page_ocr(self):
        """CHARACTER_BOX_FORMS_PROFILE must also use FULL_PAGE OCR."""
        assert CHARACTER_BOX_FORMS_PROFILE.force_full_page_ocr is True

    def test_comb_profiles_share_ocr_thresholds(self):
        """CHARACTER_BOX_FORMS and DIGITAL_PDF profiles must share identical
        OCR thresholds so the same field is detected regardless of routing."""
        assert CHARACTER_BOX_FORMS_PROFILE.det_limit_side_len == DIGITAL_PDF_PROFILE.det_limit_side_len
        assert CHARACTER_BOX_FORMS_PROFILE.det_db_thresh == pytest.approx(DIGITAL_PDF_PROFILE.det_db_thresh)
        assert CHARACTER_BOX_FORMS_PROFILE.det_db_box_thresh == pytest.approx(DIGITAL_PDF_PROFILE.det_db_box_thresh)

    def test_all_aligned_profiles_use_standard_ocr_thresholds(self):
        """All profiles the user aligned to the new standard must agree:
        det_limit_side_len=1536, det_db_thresh=0.05, det_db_box_thresh=0.2."""
        aligned = {
            "character_box_forms": CHARACTER_BOX_FORMS_PROFILE,
            "digital_pdf":         DIGITAL_PDF_PROFILE,
            "scanned_documents":   SCANNED_DOCUMENTS_PROFILE,
            "mixed_content":       MIXED_CONTENT_PROFILE,
        }
        for name, profile in aligned.items():
            assert profile.det_limit_side_len == 1536,         f"{name}: det_limit_side_len"
            assert profile.det_db_thresh      == pytest.approx(0.05), f"{name}: det_db_thresh"
            assert profile.det_db_box_thresh  == pytest.approx(0.2),  f"{name}: det_db_box_thresh"

    def test_no_profile_has_fast_table_mode(self):
        """FAST mode is disabled repo-wide; all profiles must declare ACCURATE."""
        for name, profile in DOCLING_PROFILES.items():
            assert profile.table_mode.upper() == "ACCURATE", (
                f"profile '{name}' declares table_mode={profile.table_mode!r}"
            )


class TestDigitalPdfCombBoxMerge:
    """Verifies CombBoxDetector merges per-character FULL_PAGE OCR output correctly."""

    def test_merge_6_char_alphanumeric_field(self):
        """Happy path: 6-character PAN-style field merges into one token."""
        detector = CombBoxDetector()
        elements = _make_char_elements("ABC123")
        merged = detector.detect_and_merge_comb_boxes(elements, page_number=1, doc_id="TEST")
        assert len(merged) == 1
        assert merged[0].text == "ABC123"
        assert merged[0].source_type == "comb_box_reconstruction"

    def test_merge_10_char_mobile_number(self):
        """10-digit mobile number merges into a single token."""
        detector = CombBoxDetector()
        elements = _make_char_elements("9876543210")
        merged = detector.detect_and_merge_comb_boxes(elements, page_number=1, doc_id="TEST")
        assert len(merged) == 1
        assert merged[0].text == "9876543210"

    def test_merge_produces_encompassing_bbox(self):
        """Merged bbox must span from the first to the last character box."""
        detector = CombBoxDetector()
        # 4 chars, box_w=15, gap=3, pitch=18, x0=50
        # last char x_start=50+3*18=104, x_end=104+15=119
        elements = _make_char_elements("ABCD", x0=50.0, box_w=15.0, gap=3.0)
        merged = detector.detect_and_merge_comb_boxes(elements, page_number=1, doc_id="TEST")
        assert len(merged) == 1
        assert merged[0].bbox[0] == pytest.approx(50.0)
        assert merged[0].bbox[2] == pytest.approx(50.0 + 3 * (15.0 + 3.0) + 15.0)

    def test_merge_confidence_is_average_of_constituents(self):
        """Merged confidence = mean of constituent confidences."""
        detector = CombBoxDetector()
        elements = _make_char_elements("AB12")
        for i, e in enumerate(elements):
            e.confidence = 0.8 + i * 0.05  # 0.80, 0.85, 0.90, 0.95
        merged = detector.detect_and_merge_comb_boxes(elements, page_number=1, doc_id="TEST")
        assert len(merged) == 1
        assert merged[0].confidence == pytest.approx(sum([0.80, 0.85, 0.90, 0.95]) / 4)

    def test_single_char_element_not_merged(self):
        """A single character cannot form a sequence (min_sequence_length=2)."""
        detector = CombBoxDetector()
        merged = detector.detect_and_merge_comb_boxes(
            _make_char_elements("X"), page_number=1, doc_id="TEST"
        )
        assert merged == []

    def test_word_level_element_not_a_candidate(self):
        """Word-level tokens (non-forced OCR) must fail the candidate filter."""
        assert CombBoxDetector.is_candidate_text("ABCD1234") is False

    def test_per_char_tokens_are_candidates(self):
        """Single-character and short alnum tokens must pass the candidate filter."""
        for ch in ["A", "1", "AB", "12", "A1"]:
            assert CombBoxDetector.is_candidate_text(ch) is True, f"Expected True for {ch!r}"

    def test_non_alnum_not_a_candidate(self):
        """Non-alphanumeric text is never a comb-box candidate."""
        for txt in ["-", "/", ".", "Rs.", "Name:"]:
            assert CombBoxDetector.is_candidate_text(txt) is False, f"Expected False for {txt!r}"

    def test_two_separate_fields_produce_two_tokens(self):
        """Two spatially-separated fields on the same baseline merge independently."""
        detector = CombBoxDetector()
        field1 = _make_char_elements("ABCDE", x0=50.0,  box_w=16.0, gap=4.0)
        field2 = _make_char_elements("12345", x0=400.0, box_w=16.0, gap=4.0)
        merged = detector.detect_and_merge_comb_boxes(field1 + field2, page_number=1, doc_id="TEST")
        assert len(merged) == 2
        assert {m.text for m in merged} == {"ABCDE", "12345"}

    def test_page_filter_excludes_other_pages(self):
        """Elements on a different page must not reach the merge pass."""
        detector = CombBoxDetector()
        merged = detector.detect_and_merge_comb_boxes(
            _make_char_elements("ABCD", page_number=2), page_number=1, doc_id="TEST"
        )
        assert merged == []

    def test_merged_token_metadata_contains_constituent_texts(self):
        """MergedToken.metadata must carry constituent_texts for audit tracing."""
        detector = CombBoxDetector()
        merged = detector.detect_and_merge_comb_boxes(
            _make_char_elements("XYZ"), page_number=1, doc_id="TEST"
        )
        assert len(merged) == 1
        assert merged[0].metadata.get("constituent_texts") == ["X", "Y", "Z"]

    def test_uniformity_score_in_range(self):
        """Uniformly-spaced elements must produce a uniformity_score in [0, 1]."""
        detector = CombBoxDetector()
        merged = detector.detect_and_merge_comb_boxes(
            _make_char_elements("A1B2C3"), page_number=1, doc_id="TEST"
        )
        assert len(merged) == 1
        assert 0.0 <= merged[0].uniformity_score <= 1.0
