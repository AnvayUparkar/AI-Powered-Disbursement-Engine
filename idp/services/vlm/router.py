from typing import List
from idp.models.ocr import OCRResult, OCRElement
from idp.core.config import settings
from idp.core.logging import logger, format_doc_log


class ConfidenceRouter:
    """Quality router deciding whether OCR elements require VLM verification."""

    def __init__(self, threshold: float = settings.OCR_CONFIDENCE_THRESHOLD):
        self.threshold = threshold
        self.vlm_enabled = settings.VLM_ENABLED

    def should_use_vlm(self, ocr_result: OCRResult, doc_id: str = "DOC") -> bool:
        """Determines if any element in the OCRResult requires VLM fallback."""
        if not self.vlm_enabled:
            return False

        if ocr_result.low_confidence_count > 0:
            logger.info(format_doc_log(doc_id, f"Router trigger: {ocr_result.low_confidence_count} low-confidence/handwritten elements detected on page {ocr_result.page_number}."))
            return True

        if ocr_result.average_confidence < self.threshold:
            logger.info(format_doc_log(doc_id, f"Router trigger: Page {ocr_result.page_number} avg confidence {ocr_result.average_confidence:.2f} below threshold {self.threshold}."))
            return True

        return False

    def get_low_confidence_elements(self, ocr_result: OCRResult) -> List[OCRElement]:
        """Filters OCR elements that require VLM inspection."""
        return [elem for elem in ocr_result.elements if elem.needs_vlm]
