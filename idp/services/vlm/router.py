import re
from typing import List, Dict, Any, Optional
from idp.models.ocr import OCRResult, OCRElement
from idp.models.layout import LayoutElement
from idp.models.table import TableStructure, TableCell
from idp.core.config import settings
from idp.core.logging import logger, format_doc_log


from idp.services.ocr.confidence import OCRConfidenceEvaluator


class ConfidenceRouter:
    """Quality router deciding whether OCR elements or layout regions require VLM or TrOCR verification."""

    def __init__(
        self,
        threshold: float = getattr(settings, "OCR_CONFIDENCE_THRESHOLD", 0.80),
        vlm_enabled: Optional[bool] = None,
        trocr_enabled: Optional[bool] = None,
    ):
        self.threshold = threshold
        self.vlm_enabled = vlm_enabled if vlm_enabled is not None else getattr(settings, "VLM_ENABLED", False)
        self.trocr_enabled = trocr_enabled if trocr_enabled is not None else getattr(settings, "TROCR_ENABLED", True)
        self.fallback_enabled = self.vlm_enabled or self.trocr_enabled
        self._evaluator = OCRConfidenceEvaluator()

    def should_use_vlm(self, ocr_result: OCRResult, doc_id: str = "DOC") -> bool:
        """Determines if any element in the OCRResult requires VLM fallback."""
        if not self.vlm_enabled:
            return False

        if ocr_result.total_elements == 0 or getattr(ocr_result, "extraction_failed", False):
            logger.info(format_doc_log(doc_id, f"Router trigger: Page {ocr_result.page_number} has zero elements or extraction_failed=True; escalating to VLM."))
            return True

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

    def get_low_confidence_layout_elements(
        self, elements: List[LayoutElement], doc_id: str = "DOC"
    ) -> List[LayoutElement]:
        """Filter LayoutElements needing fallback based on confidence, garbled text, or validation failure."""
        if not self.fallback_enabled:
            return []

        flagged: List[LayoutElement] = []
        for elem in elements:
            if not elem.text:
                continue

            # Condition 1: Low confidence score, garbled text, or flagged for review (e.g. comb-box/comb-grid outliers)
            if elem.confidence < self.threshold or self._evaluator.is_garbled_text(elem.text) or (elem.metadata and elem.metadata.get("needs_review")):
                flagged.append(elem)
                continue

            # Condition 2: Ambiguous KYC field (e.g. candidate PAN number failing strict regex validation)
            # Example: ABCDO1234F (has 'O' in numeric section or '0' in alpha section)
            pan_candidate = re.search(r"([A-Z0-9]{10})", elem.text.upper())
            if pan_candidate and ("PAN" in elem.text.upper() or "PERMANENT ACCOUNT" in elem.text.upper()):
                candidate_str = pan_candidate.group(1)
                is_valid_pan = bool(re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", candidate_str))
                if not is_valid_pan:
                    logger.info(format_doc_log(doc_id, f"Router trigger: PAN candidate '{candidate_str}' failed strict validation; routing to VLM."))
                    flagged.append(elem)

        return flagged
