"""Unit and integration tests for Comb-Box / Scanned Character-Form OCR Accuracy.

Validates:
1. Phase 0: Token validation routing preserving valid alphanumeric/numeric codes (03072026, APPL00243685).
2. Phase 1 & 3: Per-character confidence tracking and outlier detection (catching ARSHALI confidence dip).
3. Phase 4: Confusable-character lexicon tie-break suggestions (R -> K for AKSHALI).
4. Phase 5: CombGridDetector value run format validation (flagging BBINLKHLLU as invalid IFSC).
5. Phase 6: ConfidenceRouter escalation for elements with needs_review=True.
"""
import pytest
from idp.models.layout import LayoutElement, ElementType
from idp.services.extraction.comb_box_validator import validate_comb_box_token
from idp.services.extraction.comb_box_detector import CombBoxDetector
from idp.services.extraction.confusable_chars import suggest_confusable_corrections
from idp.services.extraction.comb_grid_detector import CombGridDetector
from idp.services.vlm.router import ConfidenceRouter


class TestPhase0ValidationRouting:
    def test_preserves_valid_numeric_codes(self):
        """Confirm 03072026 (date code) passes cleanly through comb_box_validator."""
        elem = LayoutElement(
            id="test-1",
            type=ElementType.TEXT,
            text="03072026",
            bbox=[100.0, 200.0, 180.0, 220.0],
            page_number=1,
            confidence=0.92,
            source="comb_box_merged",
        )
        assert validate_comb_box_token(elem) == "03072026"

    def test_preserves_valid_alphanumeric_application_numbers(self):
        """Confirm APPL00243685 passes cleanly without generic sanitizer mangling."""
        elem = LayoutElement(
            id="test-2",
            type=ElementType.TEXT,
            text="APPL00243685",
            bbox=[50.0, 100.0, 200.0, 120.0],
            page_number=1,
            confidence=0.89,
            source="comb_box_merged",
        )
        assert validate_comb_box_token(elem) == "APPL00243685"


class TestPhase1And3ConfidenceOutliers:
    def test_detects_confidence_outlier_in_sequence(self):
        """In 'ARSHALI', the 'R' has low confidence (0.40) while others are high (0.90+)."""
        detector = CombBoxDetector()
        chars = ["A", "R", "S", "H", "A", "L", "I"]
        confs = [0.95, 0.40, 0.94, 0.92, 0.96, 0.93, 0.95]

        elements = [
            LayoutElement(
                id=f"c-{i}",
                type=ElementType.TEXT,
                text=ch,
                bbox=[10.0 + i * 20.0, 50.0, 25.0 + i * 20.0, 70.0],
                page_number=1,
                confidence=cf,
            )
            for i, (ch, cf) in enumerate(zip(chars, confs))
        ]

        outliers = detector._detect_confidence_outliers(elements)
        assert outliers == [1], f"Expected index 1 ('R') to be detected as outlier, got {outliers}"

    def test_merged_token_carries_constituent_confidences_and_needs_review(self):
        detector = CombBoxDetector()
        chars = ["A", "R", "S", "H", "A", "L", "I"]
        confs = [0.95, 0.40, 0.94, 0.92, 0.96, 0.93, 0.95]

        elements = [
            LayoutElement(
                id=f"c-{i}",
                type=ElementType.TEXT,
                text=ch,
                bbox=[10.0 + i * 20.0, 50.0, 25.0 + i * 20.0, 70.0],
                page_number=1,
                confidence=cf,
            )
            for i, (ch, cf) in enumerate(zip(chars, confs))
        ]

        merged = detector._merge_sequence(elements, page_number=1, doc_id="TEST")
        assert merged is not None
        assert merged.metadata["constituent_confidences"] == confs
        assert merged.metadata["outlier_positions"] == [1]
        assert merged.metadata.get("needs_review") is True


class TestPhase4ConfusableLexiconTieBreak:
    def test_suggests_akshali_for_arshali_outlier(self):
        """Position 1 is 'R', which has confusable pair 'K', resolving to lexicon entry 'AKSHALI'."""
        suggestion = suggest_confusable_corrections(
            text="ARSHALI",
            outlier_positions=[1],
        )
        assert suggestion is not None
        assert suggestion["suggested_text"] == "AKSHALI"
        assert suggestion["original_char"] == "R"
        assert suggestion["suggested_char"] == "K"
        assert suggestion["rationale"] == "confusable_char_lexicon_hit"

    def test_no_suggestion_if_text_already_in_lexicon(self):
        suggestion = suggest_confusable_corrections(
            text="AKSHALI",
            outlier_positions=[1],
        )
        assert suggestion is None


class TestPhase5CombGridContentValidation:
    def test_flags_invalid_ifsc_shape(self):
        """'BBINLKHLLU' has 10 chars (or missing 0 at index 4) -> invalid IFSC."""
        is_valid, reason = CombGridDetector.validate_value_run_format("BBINLKHLLU")
        assert is_valid is False
        assert reason == "invalid_ifsc_shape"

    def test_accepts_valid_ifsc_shape(self):
        is_valid, reason = CombGridDetector.validate_value_run_format("SBIN0001234")
        assert is_valid is True
        assert reason is None

    def test_flags_invalid_pincode_with_letter(self):
        is_valid, reason = CombGridDetector.validate_value_run_format("4000O1")
        assert is_valid is False
        assert reason == "invalid_pincode_shape"

    def test_accepts_valid_pincode(self):
        is_valid, reason = CombGridDetector.validate_value_run_format("400001")
        assert is_valid is True


class TestPhase6EscalationRouting:
    def test_router_flags_element_with_needs_review(self):
        router = ConfidenceRouter(vlm_enabled=True)
        elem = LayoutElement(
            id="flagged-comb",
            type=ElementType.TEXT,
            text="ARSHALI",
            bbox=[10.0, 50.0, 150.0, 70.0],
            page_number=1,
            confidence=0.88,  # High average confidence
            metadata={"needs_review": True, "outlier_positions": [1]},
        )
        flagged = router.get_low_confidence_layout_elements([elem])
        assert len(flagged) == 1
        assert flagged[0].id == "flagged-comb"
