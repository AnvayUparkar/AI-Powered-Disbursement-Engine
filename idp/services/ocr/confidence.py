import re
from typing import List
from idp.models.ocr import OCRElement, OCRResult
from idp.core.config import settings


class OCRConfidenceEvaluator:
    """Evaluates OCR element confidence scores, suspicious patterns, and handwriting flags."""

    # Patterns indicating potential handwriting or garbled OCR output
    HANDWRITING_PATTERNS = [
        re.compile(r"^[~`!@#$%^&*()_+={}\[\]|\\:;\"'<>,?\/]+$"),  # Garbage non-alphanumeric text
        re.compile(r"(.)\1{4,}"),                                  # Repeated characters (e.g. "aaaaa")
        re.compile(r"^[0-9a-zA-Z]{1,2}$")                         # Isolated single/double floating noisy chars
    ]

    def __init__(self, threshold: float = settings.OCR_CONFIDENCE_THRESHOLD):
        self.threshold = threshold

    def evaluate_element(self, element: OCRElement) -> OCRElement:
        """Evaluate a single OCR element and mark if VLM inspection is required."""
        text = element.text.strip()

        # Check threshold
        if element.confidence < self.threshold:
            element.needs_vlm = True

        # Check length / garbage character density
        if len(text) > 0:
            for pattern in self.HANDWRITING_PATTERNS:
                if pattern.match(text):
                    element.needs_vlm = True
                    break

        return element

    def evaluate_result(self, result: OCRResult) -> OCRResult:
        """Evaluate aggregate OCR result for a page."""
        low_count = 0
        total_conf = 0.0

        for elem in result.elements:
            self.evaluate_element(elem)
            if elem.needs_vlm:
                low_count += 1
            total_conf += elem.confidence

        result.low_confidence_count = low_count
        result.total_elements = len(result.elements)
        result.average_confidence = (total_conf / len(result.elements)) if result.elements else 1.0

        return result
