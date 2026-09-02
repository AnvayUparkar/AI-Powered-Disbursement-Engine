from idp.models.ocr import OCRElement, OCRResult
from idp.services.ocr.confidence import OCRConfidenceEvaluator
from idp.services.vlm.router import ConfidenceRouter


def test_confidence_evaluation_and_router():
    evaluator = OCRConfidenceEvaluator(threshold=0.70)
    router = ConfidenceRouter(threshold=0.70)

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
