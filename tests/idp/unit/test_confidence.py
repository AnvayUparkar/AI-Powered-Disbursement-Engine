from idp.models.ocr import OCRElement, OCRResult
from idp.services.ocr.confidence import OCRConfidenceEvaluator
from idp.services.vlm.router import ConfidenceRouter
import pytest
from unittest.mock import MagicMock


def test_confidence_evaluation_and_router():
    evaluator = OCRConfidenceEvaluator(threshold=0.70)
    router = ConfidenceRouter(threshold=0.70, vlm_enabled=True)

    high_conf_elem = OCRElement(
        id="1", text="Clean Text", bbox=[0, 0, 10, 10], confidence=0.95, page_number=1
    )
    low_conf_elem = OCRElement(
        id="2", text="~~~~~", bbox=[0, 0, 10, 10], confidence=0.40, page_number=1
    )

    result = OCRResult(page_number=1, elements=[high_conf_elem, low_conf_elem])
    evaluated = evaluator.evaluate_result(result)

    assert evaluated.low_confidence_count == 1
    assert router.should_use_vlm(evaluated) is True
    assert len(router.get_low_confidence_elements(evaluated)) == 1


def test_compute_text_confidence_clean_inputs():
    evaluator = OCRConfidenceEvaluator(threshold=0.80)

    # Standard clean titles and names must have high confidence (>= 0.95)
    assert evaluator.compute_text_confidence("Mr.") >= 0.95
    assert evaluator.compute_text_confidence("Dr.") >= 0.95
    assert evaluator.compute_text_confidence("Spouse Name Title") >= 0.95
    assert evaluator.compute_text_confidence("GYAN CHAND KHATRI") >= 0.95
    assert evaluator.compute_text_confidence("Loan Application Form") >= 0.95


def test_compute_text_confidence_ocr_artifacts():
    evaluator = OCRConfidenceEvaluator(threshold=0.80)

    # 1. Clipped leading letter + glued casing (e.g. "pplicant NamePRAKASHKHATRI")
    score_clipped = evaluator.compute_text_confidence("pplicant NamePRAKASHKHATRI")
    assert score_clipped < 0.80, f"Expected < 0.80 for clipped/glued text, got {score_clipped}"

    # 2. Sandwiched lowercase inside uppercase (e.g. "Mother's Name KAmLA")
    score_sandwiched = evaluator.compute_text_confidence("Mother's Name KAmLA")
    assert score_sandwiched < 0.85, f"Expected < 0.85 for sandwiched casing, got {score_sandwiched}"
    assert score_sandwiched < evaluator.compute_text_confidence("Mother's Name KAMLA")

    # 3. Digit inside word (e.g. "L0AN F0RM")
    score_digit = evaluator.compute_text_confidence("L0AN F0RM")
    assert score_digit < 0.85, f"Expected < 0.85 for digit in word, got {score_digit}"


def test_compute_text_confidence_edge_cases():
    evaluator = OCRConfidenceEvaluator(threshold=0.80)

    # Empty and whitespace
    assert evaluator.compute_text_confidence("") == 0.0
    assert evaluator.compute_text_confidence("   ") == 0.0

    # Garbled noise
    assert evaluator.compute_text_confidence("~~~~~") <= 0.40
    assert evaluator.compute_text_confidence("HRTRR") <= 0.40
    assert evaluator.compute_text_confidence("!@#$%^&*") <= 0.40

    # Degenerate zero-size bounding box
    score_normal = evaluator.compute_text_confidence("Valid Text", bbox=[10.0, 10.0, 100.0, 50.0])
    score_zero_bbox = evaluator.compute_text_confidence("Valid Text", bbox=[10.0, 10.0, 10.0, 10.0])
    assert score_zero_bbox < score_normal


def test_docling_parser_applies_dynamic_confidence(monkeypatch):
    """DoclingParser must compute dynamic confidence rather than hardcoding 1.0."""
    from idp.services.docling.parser import DoclingParser
    from unittest.mock import MagicMock

    parser = DoclingParser()

    class MockItem:
        def __init__(self, text, label="text"):
            self.text = text
            self.label = label
            self.prov = []

    class MockDoc:
        def __init__(self):
            self.pages = {}
            self.texts = [
                MockItem("Spouse Name Title"),
                MockItem("pplicant NamePRAKASHKHATRI"),
            ]
            self.tables = []

    mock_converter = MagicMock()
    mock_conv_result = MagicMock()
    mock_conv_result.document = MockDoc()
    mock_converter.convert.return_value = mock_conv_result

    monkeypatch.setattr(parser.pipeline, "get_converter", lambda: mock_converter)

    result = parser.parse("dummy.pdf", doc_id="TEST-DOC")

    assert len(result.elements) == 2
    # Clean text has high score
    assert result.elements[0].confidence >= 0.95
    # Defective OCR text has lower dynamic score, not 1.0
    assert result.elements[1].confidence < 0.80
    assert result.elements[0].confidence != result.elements[1].confidence


# === NEW TESTS FOR BOUNDING BOX FORMATION FIX ===

def test_degenerate_bbox_all_zero_is_flagged():
    """Previously skipped [0,0,0,0] boxes due to guard bug - now properly flagged."""
    evaluator = OCRConfidenceEvaluator(threshold=0.70)
    
    # Test the fixed _is_bbox_unformed method directly
    assert evaluator._is_bbox_unformed([0, 0, 0, 0]) is True
    assert evaluator._is_bbox_unformed([0.0, 0.0, 0.0, 0.0]) is True
    assert evaluator._is_bbox_unformed(None) is True
    assert evaluator._is_bbox_unformed([]) is True
    assert evaluator._is_bbox_unformed([10, 20]) is True  # Too few coords
    
    # Test penalty is applied in compute_text_confidence
    score_normal = evaluator.compute_text_confidence("Valid Text", bbox=[10, 10, 100, 50])
    score_zero = evaluator.compute_text_confidence("Valid Text", bbox=[0, 0, 0, 0])
    
    # Zero bbox should have 0.30 penalty applied
    assert score_zero < score_normal
    assert abs(score_normal - score_zero) >= 0.25  # Should see substantial penalty


def test_none_bbox_still_skippable_when_not_passed():
    """Preserve existing behavior for callers that don't provide bbox."""
    evaluator = OCRConfidenceEvaluator(threshold=0.70)
    
    # These should have identical scores - no penalty when bbox=None
    score_no_bbox = evaluator.compute_text_confidence("Valid Text")
    score_none_bbox = evaluator.compute_text_confidence("Valid Text", bbox=None)
    
    assert score_no_bbox == score_none_bbox


def test_layout_passthrough_empty_text():
    """Elements with valid bbox but empty/generic text should be flagged."""
    evaluator = OCRConfidenceEvaluator(threshold=0.70)
    
    # Mock element with valid bbox but empty text
    element = MagicMock()
    element.text = ""
    element.bbox = [10, 10, 100, 50]
    element.metadata = {}
    element.label = "Text Block"
    element.source = "docling"
    
    assert evaluator.is_layout_passthrough(element) is True
    
    # Test with text that equals the generic label
    element.text = "Text Block"
    assert evaluator.is_layout_passthrough(element) is True
    
    # Test with other passthrough labels
    element.text = "Picture"
    assert evaluator.is_layout_passthrough(element) is True


def test_layout_passthrough_label_echo():
    """Text that echoes the layout label should be flagged as passthrough."""
    evaluator = OCRConfidenceEvaluator(threshold=0.70)
    
    element = MagicMock()
    element.bbox = [10, 10, 100, 50]
    element.metadata = {}
    element.source = "docling"
    
    # Test all passthrough labels
    for label in ["Text Block", "Picture", "Container", "Form Field"]:
        element.text = label
        element.label = label
        assert evaluator.is_layout_passthrough(element) is True, f"Failed for label: {label}"


def test_layout_passthrough_disproportionate_box():
    """Very wide boxes for short text should be flagged as layout containers."""
    evaluator = OCRConfidenceEvaluator(threshold=0.70)
    
    element = MagicMock()
    element.metadata = {}
    element.label = "text"
    element.source = "rapidocr"
    
    # Short text in very wide box (logo case)
    element.text = "HDB"  # 3 characters
    element.bbox = [10, 10, 200, 30]  # Width=190, Height=20, expected_width ≈ 3*20*0.55=33
    # Actual width (190) > expected_width*4 (33*4=132), should be flagged
    
    assert evaluator.is_layout_passthrough(element) is True
    
    # Same text in appropriately sized box should not be flagged
    element.bbox = [10, 10, 50, 30]  # Width=40, reasonable for 3 chars
    assert evaluator.is_layout_passthrough(element) is False


def test_real_ocr_token_not_flagged():
    """Valid OCR elements should not be flagged as passthrough."""
    evaluator = OCRConfidenceEvaluator(threshold=0.70)
    
    element = MagicMock()
    element.text = "Applicant Name"
    element.bbox = [10, 10, 120, 30]  # Reasonable size for text
    element.metadata = {"ocr_engine": "rapidocr"}
    element.label = "text"
    element.source = "rapidocr"
    
    assert evaluator.is_layout_passthrough(element) is False


def test_reconstruction_from_neighbors():
    """Passthrough elements should reconstruct bbox from valid sibling tokens."""
    evaluator = OCRConfidenceEvaluator(threshold=0.70)
    
    # Create passthrough element (wide container)
    passthrough = MagicMock()
    passthrough.text = "Text Block"
    passthrough.bbox = [0, 100, 200, 120]  # Wide container
    passthrough.metadata = {}
    passthrough.label = "Text Block"
    passthrough.source = "docling"
    
    # Create valid neighbor elements within the container
    neighbor1 = MagicMock()
    neighbor1.text = "Business"
    neighbor1.bbox = [10, 105, 50, 115]
    neighbor1.metadata = {}
    neighbor1.source = "rapidocr"
    
    neighbor2 = MagicMock()
    neighbor2.text = "Self employed"
    neighbor2.bbox = [60, 105, 120, 115]
    neighbor2.metadata = {}
    neighbor2.source = "rapidocr"
    
    neighbor3 = MagicMock()
    neighbor3.text = "Professional"
    neighbor3.bbox = [130, 105, 190, 115]
    neighbor3.metadata = {}
    neighbor3.source = "rapidocr"
    
    all_elements = [passthrough, neighbor1, neighbor2, neighbor3]
    
    # Reconstruct bbox
    reconstructed = evaluator.reconstruct_bbox_from_row_neighbors(
        passthrough, all_elements
    )
    
    # Should return union of neighbor boxes: [10, 105, 190, 115]
    assert reconstructed is not None
    assert reconstructed[0] == 10  # min x
    assert reconstructed[1] == 105  # min y
    assert reconstructed[2] == 190  # max x
    assert reconstructed[3] == 115  # max y
    
    # Check metadata was updated
    assert passthrough.metadata["bbox_source"] == "reconstructed_from_neighbors"
    assert passthrough.metadata["bbox_reconstruction_token_count"] == 3


def test_reconstruction_no_candidates_logo_case():
    """Passthrough with no valid neighbors should keep original bbox and flag as logo."""
    evaluator = OCRConfidenceEvaluator(threshold=0.70)
    
    # Logo element with no text neighbors
    logo = MagicMock()
    logo.text = "Picture"
    logo.bbox = [50, 50, 100, 80]
    logo.metadata = {}
    logo.label = "Picture"
    logo.source = "docling"
    
    # No neighbor elements
    all_elements = [logo]
    
    reconstructed = evaluator.reconstruct_bbox_from_row_neighbors(logo, all_elements)
    
    # Should return original bbox
    assert reconstructed == [50, 50, 100, 80]
    
    # Should be marked as fallback
    assert logo.metadata["bbox_source"] == "layout_fallback_no_text"
    assert logo.metadata["bbox_reconstruction_token_count"] == 0


def test_evaluate_element_sets_needs_vlm_and_flag():
    """Passthrough elements should be marked for VLM review with appropriate metadata."""
    evaluator = OCRConfidenceEvaluator(threshold=0.70)
    
    # Create passthrough element
    element = OCRElement(
        id="test",
        text="Text Block",
        bbox=[10, 10, 200, 30],
        confidence=0.95,  # High confidence but still passthrough
        page_number=1,
        metadata={}
    )
    element.label = "Text Block"
    element.source = "docling"
    
    # Evaluate the element
    evaluated = evaluator.evaluate_element(element)
    
    # Should be flagged for VLM despite high confidence
    assert evaluated.needs_vlm is True
    assert evaluated.metadata["layout_passthrough"] is True


def test_no_regression_on_clean_element():
    """Standard valid elements should be unaffected by the fix."""
    evaluator = OCRConfidenceEvaluator(threshold=0.70)
    
    # Clean, valid element
    element = OCRElement(
        id="clean",
        text="Applicant Name",
        bbox=[10, 10, 100, 25],
        confidence=0.95,
        page_number=1,
        metadata={"ocr_engine": "rapidocr"}
    )
    element.source = "rapidocr"
    
    # Evaluate element
    evaluated = evaluator.evaluate_element(element)
    
    # Should not be flagged as passthrough or needing VLM
    assert evaluated.needs_vlm is False
    assert "layout_passthrough" not in evaluated.metadata
    
    # Confidence should remain high
    assert evaluated.confidence >= 0.90


def test_bbox_malformed_edge_cases():
    """Test robustness against various malformed bbox inputs."""
    evaluator = OCRConfidenceEvaluator(threshold=0.70)
    
    # Test various malformed bbox cases
    malformed_cases = [
        None,
        [],
        [10],
        [10, 20],
        [10, 20, 30],
        ["a", "b", "c", "d"],  # Non-numeric
        [float('inf'), 0, 10, 10],  # Infinity
        [float('nan'), 0, 10, 10],  # NaN
    ]
    
    for bbox in malformed_cases:
        assert evaluator._is_bbox_unformed(bbox) is True, f"Should flag malformed bbox: {bbox}"

        # Should not crash in compute_text_confidence
        score = evaluator.compute_text_confidence("Valid Text", bbox=bbox)
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0


# === FORM-FIELD LOSS PREVENTION (loan application form) ===
# Regression coverage for whole rows / field values being dropped because the
# fused single-line OCR string tripped is_garbled_text() or was gutted by
# clean_bilingual_label_noise() before the serializer ever kept the element.


def test_form_abbreviations_not_flagged_as_garbled():
    """Printed loan-form labels / checkbox options must survive the garble gate."""
    evaluator = OCRConfidenceEvaluator(threshold=0.70)

    kept = [
        "Type of company Pvt. Ltd. Public Ltd. Ltd. Liability Co.",
        "PSU Govt. MNC Other",
        "Preferred mode of Communication : Whatsapp SMS E Mail",
        "CKYC No. UPIID/VPA",
        "STD",
        "HDB FINANCIAL SERVICES",
        "GSTIN available: Yes No",
    ]
    for row in kept:
        cleaned = OCRConfidenceEvaluator.clean_bilingual_label_noise(row)
        assert cleaned.strip(), f"row was gutted to empty: {row!r}"
        assert not evaluator.is_garbled_text(cleaned), f"falsely flagged garbled: {row!r} -> {cleaned!r}"


def test_true_devanagari_garble_still_flagged():
    """The fix must not blunt detection of real Devanagari->Latin OCR noise."""
    evaluator = OCRConfidenceEvaluator(threshold=0.70)

    for noise in ["3TET", "3RRTO xyz", "HRTRR", "RROR", "~~~~~", "!@#$%^&*"]:
        assert evaluator.is_garbled_text(noise) is True, f"should still be garbled: {noise!r}"


def test_clean_bilingual_preserves_alphanumeric_field_values():
    """Application no. / GSTIN / account no. / STD code are real values, not label garble."""
    clean = OCRConfidenceEvaluator.clean_bilingual_label_noise

    # Application number must not be stripped out of the row
    assert "APPL00343265" in clean("Application Date:06082026 ApplINo.:APPL00343265")
    # GSTIN value retained
    assert "08AOOPK6924P1ZZ" in clean("GSTIN available: Yes No GSTIN No. 08AOOPK6924P1ZZ")
    # Bundled STD code + number retained (was previously wiped to "")
    assert clean("STD0141").strip() == "STD0141"
    # Account number retained
    assert "50200064998229" in clean("Account Number 50200064998229")
    # Bank / PAN run retained
    assert "PANA00PK6924P" in clean("Mobile9079533555 PANA00PK6924P")


def test_clean_bilingual_still_strips_short_misread_blobs():
    """Short, letter-heavy Devanagari blobs are still removed (contract preserved)."""
    clean = OCRConfidenceEvaluator.clean_bilingual_label_noise

    assert clean("FarHToT 3RRTO INCOME TAX DEPARTMENT") == "INCOME TAX DEPARTMENT"
    assert "3TET" not in clean("Name 3TET RAMESH")
    assert "9HTET" not in clean("Address 9HTET LINE")


def test_clean_bilingual_keeps_middle_initial_and_split_option():
    """Lone uppercase letter flanked by words is a middle initial / split label, not noise."""
    clean = OCRConfidenceEvaluator.clean_bilingual_label_noise

    assert clean("RAJESH K SHARMA") == "RAJESH K SHARMA"
    # leading isolated noise letter is still removed
    assert clean("R BAZAR DIST JAIPUR").startswith("BAZAR")
