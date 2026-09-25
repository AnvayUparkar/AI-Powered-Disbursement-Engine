"""
Adapter to convert LightOnOCR output to OCRElement/OCRResult format.

Ensures compatibility with existing serializer and deduplication logic.
"""
from typing import List, Optional
from idp.models.ocr import OCRElement, OCRResult
from idp.services.ocr.lightonocr_engine import LightOnOCRResult, get_lightonocr_engine
from idp.core.config import settings
from idp.core.logging import logger, format_doc_log


class LightOnOCRAdapter:
    """Convert LightOnOCR results to standard OCR format."""
    
    def __init__(self) -> None:
        self.engine = get_lightonocr_engine()
        self.quality_threshold: float = settings.LIGHTONOCR_QUALITY_THRESHOLD
        self.confidence_threshold: float = settings.OCR_CONFIDENCE_THRESHOLD
    
    def process_page_to_ocr_result(
        self,
        image_bytes: bytes,
        page_number: int,
        image_width: float,
        image_height: float,
        doc_id: str = "DOC"
    ) -> Optional[OCRResult]:
        """
        Process a scanned page through LightOnOCR and return OCRResult.
        
        Args:
            image_bytes: Page image
            page_number: Page number (1-indexed)
            image_width: Image pixel width
            image_height: Image pixel height
            doc_id: Document ID
        
        Returns:
            OCRResult or None on failure
        """
        # Run LightOnOCR
        lightonocr_result = self.engine.process_page(image_bytes, page_number, doc_id)
        
        if lightonocr_result is None:
            return self._create_failed_ocr_result(page_number, image_width, image_height)
        
        # Check quality
        if lightonocr_result.quality_score < self.quality_threshold:
            logger.warning(format_doc_log(
                doc_id,
                f"LightOnOCR page {page_number} quality score {lightonocr_result.quality_score:.2f} "
                f"below threshold {self.quality_threshold} - will trigger VLM fallback"
            ))
            # Return result but mark as low quality
            return self._convert_to_ocr_result(
                lightonocr_result, 
                page_number, 
                image_width, 
                image_height,
                low_quality=True
            )
        
        # Quality OK - return result
        return self._convert_to_ocr_result(
            lightonocr_result,
            page_number,
            image_width,
            image_height,
            low_quality=False
        )
    
    def _convert_to_ocr_result(
        self,
        lightonocr_result: LightOnOCRResult,
        page_number: int,
        image_width: float,
        image_height: float,
        low_quality: bool = False
    ) -> OCRResult:
        """
        Convert LightOnOCRResult to OCRResult format.
        
        Strategy:
        - If LightOnOCR provides bboxes -> create OCRElement per bbox
        - If no bboxes -> create single OCRElement with full-page bbox
        """
        elements: List[OCRElement] = []
        
        text = lightonocr_result.text.strip()
        confidence = lightonocr_result.confidence
        
        # If quality is low or confidence < threshold, mark for VLM review
        needs_vlm = low_quality or confidence < self.confidence_threshold
        
        if lightonocr_result.bboxes and len(lightonocr_result.bboxes) > 0:
            # LightOnOCR provided bounding boxes
            for idx, bbox in enumerate(lightonocr_result.bboxes):
                # Extract text segment (if text segmentation info available)
                elem_text = text if idx == 0 else ""
                
                if elem_text:  # Skip empty segments
                    elem = OCRElement(
                        id=f"lightonocr-p{page_number}-{idx}",
                        text=elem_text,
                        bbox=bbox,  # [x1, y1, x2, y2]
                        confidence=confidence,
                        page_number=page_number,
                        source="lightonocr",
                        needs_vlm=needs_vlm
                    )
                    elements.append(elem)
        else:
            # No bboxes provided - create single full-page element
            elem = OCRElement(
                id=f"lightonocr-p{page_number}-full",
                text=text,
                bbox=[0.0, 0.0, float(image_width), float(image_height)],
                confidence=confidence,
                page_number=page_number,
                source="lightonocr",
                needs_vlm=needs_vlm
            )
            elements.append(elem)
        
        # Compute statistics
        total_elements = len(elements)
        low_conf_count = sum(1 for e in elements if e.needs_vlm)
        avg_conf = confidence
        
        return OCRResult(
            page_number=page_number,
            elements=elements,
            average_confidence=avg_conf,
            low_confidence_count=low_conf_count,
            total_elements=total_elements,
            extraction_failed=False,
            image_width=image_width,
            image_height=image_height
        )
    
    def _create_failed_ocr_result(
        self,
        page_number: int,
        image_width: float,
        image_height: float
    ) -> OCRResult:
        """Create OCRResult marking extraction as failed."""
        return OCRResult(
            page_number=page_number,
            elements=[],
            average_confidence=0.0,
            low_confidence_count=0,
            total_elements=0,
            extraction_failed=True,
            image_width=image_width,
            image_height=image_height
        )
