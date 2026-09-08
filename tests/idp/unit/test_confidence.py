from idp.models.ocr import OCRElement, OCRResult
from idp.services.ocr.confidence import OCRConfidenceEvaluator
from idp.services.vlm.router import ConfidenceRouter


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
