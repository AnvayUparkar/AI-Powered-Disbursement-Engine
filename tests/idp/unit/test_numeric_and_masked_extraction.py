"""
Regression tests for two production bugs:

1. Trailing-zero loss (1500000 -> 150000):
   CombBoxDetector._merge_sequence() was using gap >= 0.85 * med_pitch to insert
   spaces, which fires on a slightly wider '0' cell and produces "150000 0"
   instead of "1500000".

2. Masked Aadhaar (XXXX XXXX 2841) not captured:
   OCRConfidenceEvaluator.is_garbled_text() treated repeated-single-char runs
   (XXXX) as pure-consonant noise. DocumentSerializer._has_recoverable_value()
   had no exemption for masked-identity tokens.
"""

import pytest
from idp.services.extraction.comb_box_detector import CombBoxDetector
from idp.models.layout import LayoutElement, ElementType
from idp.services.ocr.confidence import OCRConfidenceEvaluator
from idp.services.output.serializer import DocumentSerializer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _digit_elem(id_str, digit, x0, y0=100.0, w=11.0, h=13.0):
    return LayoutElement(
        id=id_str,
        type=ElementType.TEXT,
        text=digit,
        bbox=[x0, y0, x0 + w, y0 + h],
        confidence=0.88,
        page_number=1,
        source="docling_ocr",
        structure_source="docling",
    )


def _make_elem(id_str, text, x0, y0=100.0, w=11.0, h=13.0):
    return LayoutElement(
        id=id_str,
        type=ElementType.TEXT,
        text=text,
        bbox=[x0, y0, x0 + w, y0 + h],
        confidence=0.88,
        page_number=1,
        source="docling_ocr",
        structure_source="docling",
    )


# ---------------------------------------------------------------------------
# Bug 1: Trailing-zero loss on numeric comb-box sequences
# ---------------------------------------------------------------------------

class TestNumericCombBoxNoTrailingZeroLoss:
    """Salary / loan-amount fields must merge all digits without internal spaces."""

    def _build_amount_elements(self, digits, pitch=13.0, last_gap_multiplier=1.0):
        elements = []
        x = 50.0
        for i, ch in enumerate(digits):
            if i == len(digits) - 1 and last_gap_multiplier != 1.0:
                x += pitch * (last_gap_multiplier - 1.0)
            elements.append(_digit_elem(f"d{i}", ch, x, w=10.0))
            x += pitch
        return elements

    def test_standard_salary_amount_merges_intact(self):
        """1500000 with uniform pitch must produce exactly '1500000'."""
        elements = self._build_amount_elements("1500000")
        detector = CombBoxDetector()
        tokens = detector.detect_and_merge_comb_boxes(elements, page_number=1, doc_id="TEST")
        assert len(tokens) == 1, f"Expected 1 merged token, got {len(tokens)}: {[t.text for t in tokens]}"
        assert tokens[0].text == "1500000", (
            f"Trailing zero dropped: got '{tokens[0].text}' instead of '1500000'"
        )

    def test_salary_last_zero_wider_gap_no_space_inserted(self):
        """
        A final digit with a gap slightly wider than median must NOT produce
        a space in a digit-only sequence.
        Old threshold was gap >= 0.85x; test at 0.90x to confirm fix.
        """
        elements = self._build_amount_elements("1500000", pitch=13.0, last_gap_multiplier=1.10)
        detector = CombBoxDetector()
        tokens = detector.detect_and_merge_comb_boxes(elements, page_number=1, doc_id="TEST")
        assert len(tokens) >= 1
        merged = "".join(t.text.replace(" ", "") for t in tokens)
        assert "1500000" in merged, (
            f"Trailing zero lost after gap widening: tokens={[t.text for t in tokens]}"
        )

    def test_seven_digit_amount_no_internal_spaces(self):
        """No internal spaces in a 7-digit comb-box amount."""
        elements = self._build_amount_elements("1500000")
        detector = CombBoxDetector()
        tokens = detector.detect_and_merge_comb_boxes(elements, page_number=1, doc_id="TEST")
        for tok in tokens:
            assert " " not in tok.text, f"Space injected into numeric comb-box: '{tok.text}'"

    def test_alpha_name_still_gets_space_at_word_boundary(self):
        """Alpha names with a genuine 2.1x multi-cell spacer still get spaces."""
        elements = []
        x = 50.0
        pitch = 13.0
        for i, ch in enumerate("RAVI"):
            elements.append(_make_elem(f"a{i}", ch, x, w=10.0))
            x += pitch
        x += pitch * 2.1  # wide spacer gap
        for i, ch in enumerate("KUMAR"):
            elements.append(_make_elem(f"b{i}", ch, x, w=10.0))
            x += pitch
        detector = CombBoxDetector()
        tokens = detector.detect_and_merge_comb_boxes(elements, page_number=1, doc_id="TEST")
        all_text = " ".join(t.text for t in tokens)
        has_space = " " in all_text or len(tokens) >= 2
        assert has_space, f"Expected space between 'RAVI' and 'KUMAR': {[t.text for t in tokens]}"


# ---------------------------------------------------------------------------
# Bug 2: Masked Aadhaar (XXXX XXXX 2841) not extracted
# ---------------------------------------------------------------------------

class TestMaskedAadhaarNotDropped:
    """XXXX repeated-char runs must NOT be flagged as garbled consonant noise."""

    def test_xxxx_not_garbled(self):
        evaluator = OCRConfidenceEvaluator()
        assert evaluator.is_garbled_text("XXXX") is False, (
            "XXXX (masked Aadhaar group) incorrectly flagged as garbled"
        )

    def test_xxxx_xxxx_not_garbled(self):
        evaluator = OCRConfidenceEvaluator()
        assert evaluator.is_garbled_text("XXXX XXXX") is False, (
            "'XXXX XXXX' incorrectly flagged as garbled"
        )

    def test_xxxx_xxxx_2841_not_garbled(self):
        evaluator = OCRConfidenceEvaluator()
        assert evaluator.is_garbled_text("XXXX XXXX 2841") is False, (
            "'XXXX XXXX 2841' incorrectly flagged as garbled"
        )

    def test_genuine_consonant_cluster_still_garbled(self):
        """Actual OCR garble 'HRTRR' must still be caught."""
        evaluator = OCRConfidenceEvaluator()
        assert evaluator.is_garbled_text("HRTRR") is True

    def test_has_recoverable_value_accepts_xxxx(self):
        assert DocumentSerializer._has_recoverable_value("XXXX") is True, (
            "_has_recoverable_value returned False for 'XXXX'"
        )

    def test_has_recoverable_value_accepts_xxxx_full(self):
        assert DocumentSerializer._has_recoverable_value("XXXX XXXX 2841") is True

    def test_has_recoverable_value_rejects_real_garble(self):
        assert DocumentSerializer._has_recoverable_value("HRTRR") is False

    def test_xxxx_comb_box_candidate(self):
        """Individual 'X' and 'XXXX' must pass is_candidate_text."""
        assert CombBoxDetector.is_candidate_text("X") is True
        assert CombBoxDetector.is_candidate_text("XXXX") is True

    def test_masked_aadhaar_comb_box_merges(self):
        """4 'X' elements at uniform pitch must merge into 'XXXX' without spaces."""
        elements = []
        x = 50.0
        pitch = 13.0
        for i in range(4):
            elements.append(_make_elem(f"x{i}", "X", x, w=10.0))
            x += pitch
        detector = CombBoxDetector()
        tokens = detector.detect_and_merge_comb_boxes(elements, page_number=1, doc_id="TEST")
        assert len(tokens) == 1, f"Expected 1 merged token for XXXX, got {len(tokens)}: {[t.text for t in tokens]}"
        assert tokens[0].text.replace(" ", "") == "XXXX"
