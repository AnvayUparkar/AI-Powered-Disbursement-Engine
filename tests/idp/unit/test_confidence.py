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


def test_docling_parser_does_not_synthesise_confidence_from_text(monkeypatch):
    """Confidence must come from the models, never from the shape of the text.

    The old rule-based scorer returned a near-constant 0.96 for clean text and 0.35 for
    anything it judged garbled, which made the UI's confidence column meaningless. With it
    gone, two items of very different textual "quality" must score identically when Docling
    reports no layout prediction for them.
    """
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
                MockItem("Spouse Name Title"),          # would have scored ~0.96
                MockItem("pplicant NamePRAKASHKHATRI"),  # would have scored <0.80
            ]
            self.tables = []

    mock_conv_result = MagicMock()
    mock_conv_result.document = MockDoc()
    mock_conv_result.pages = []          # no layout prediction available
    mock_conv_result.confidence = None   # no per-stage scores available
    mock_converter = MagicMock()
    mock_converter.convert.return_value = mock_conv_result

    monkeypatch.setattr(parser.pipeline, "get_converter", lambda: mock_converter)
    result = parser.parse("dummy.pdf", doc_id="TEST-DOC")

    assert len(result.elements) == 2
    # Text shape no longer influences the score at all.
    assert result.elements[0].confidence == result.elements[1].confidence
    # With no model score available the real fields stay explicitly unknown rather than guessed.
    assert result.elements[0].ocr_confidence is None
    assert result.elements[0].layout_confidence is None
    # Per-stage scores are absent, not fabricated.
    assert result.layout_score is None
    assert result.ocr_score is None
    assert result.table_score is None
