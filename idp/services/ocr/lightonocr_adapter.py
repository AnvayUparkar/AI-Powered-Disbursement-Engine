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
        
        if lightonocr_result is None or lightonocr_result.hard_fail_reason is not None:
            if lightonocr_result and lightonocr_result.hard_fail_reason:
                logger.warning(format_doc_log(
                    doc_id,
                    f"LightOnOCR page {page_number} hard fail: '{lightonocr_result.hard_fail_reason}' - marking extraction failed"
                ))
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
                image_bytes=image_bytes,
                low_quality=True
            )
        
        # Quality OK - return result
        return self._convert_to_ocr_result(
            lightonocr_result,
            page_number,
            image_width,
            image_height,
            image_bytes=image_bytes,
            low_quality=False
        )
    
    def _split_into_logical_lines(self, text: str) -> List[str]:
        """
        Split OCR output into logical lines.
        Preserves markdown table rows as single elements per row.
        """
        raw_lines = text.split("\n")
        logical_lines: List[str] = []
        for line in raw_lines:
            stripped = line.strip()
            if not stripped:
                continue
            logical_lines.append(stripped)
        return logical_lines

    def _detect_line_boxes(
        self,
        image_bytes: bytes,
        image_width: float,
        image_height: float
    ) -> List[List[float]]:
        """
        Run PP-OCR detection only (RapidOCR(det=True, rec=False)) on the page image
        to obtain line bounding boxes [l, t, r, b].
        """
        boxes: List[List[float]] = []
        try:
            from rapidocr_onnxruntime import RapidOCR
            import cv2
            import numpy as np

            nparr = np.frombuffer(image_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is not None:
                engine = RapidOCR(det=True, rec=False)
                det_results, _ = engine(img)
                if det_results:
                    for quad in det_results:
                        xs = [pt[0] for pt in quad]
                        ys = [pt[1] for pt in quad]
                        l = max(0.0, float(min(xs)))
                        t = max(0.0, float(min(ys)))
                        r = min(float(image_width), float(max(xs)))
                        b = min(float(image_height), float(max(ys)))
                        if r > l and b > t:
                            boxes.append([l, t, r, b])
        except Exception as e:
            logger.debug(f"RapidOCR line detection failed/skipped: {e}")

        # Sort detected boxes by top coordinate, then left
        boxes.sort(key=lambda b: (b[1], b[0]))
        return boxes

    def _convert_to_ocr_result(
        self,
        lightonocr_result: LightOnOCRResult,
        page_number: int,
        image_width: float,
        image_height: float,
        image_bytes: Optional[bytes] = None,
        low_quality: bool = False
    ) -> OCRResult:
        """
        Convert LightOnOCRResult to OCRResult format.
        
        Strategy:
        - Split text into lines (keeping markdown table rows intact)
        - If bboxes provided by engine -> map to lines
        - Otherwise, align lines with PP-OCR line detections; unmatched lines get full-page bbox with bbox_estimated=True
        """
        elements: List[OCRElement] = []
        
        text = lightonocr_result.text.strip()
        confidence = lightonocr_result.confidence
        needs_vlm = low_quality or confidence < self.confidence_threshold

        lines = self._split_into_logical_lines(text)
        if not lines and text:
            lines = [text]

        if lightonocr_result.bboxes and len(lightonocr_result.bboxes) == len(lines):
            for idx, (line_text, bbox) in enumerate(zip(lines, lightonocr_result.bboxes)):
                elem = OCRElement(
                    id=f"lightonocr-p{page_number}-{idx}",
                    text=line_text,
                    bbox=bbox,
                    confidence=confidence,
                    page_number=page_number,
                    line_number=idx + 1,
                    source="lightonocr",
                    needs_vlm=needs_vlm,
                    metadata={"bbox_estimated": False}
                )
                elements.append(elem)
        else:
            detected_boxes: List[List[float]] = []
            if image_bytes:
                detected_boxes = self._detect_line_boxes(image_bytes, image_width, image_height)

            full_page_bbox = [0.0, 0.0, float(image_width), float(image_height)]

            for idx, line_text in enumerate(lines):
                if idx < len(detected_boxes):
                    bbox = detected_boxes[idx]
                    bbox_estimated = False
                    location_available = True
                else:
                    # Do not invent synthetic spatial bands. Keep unlocalized bounding box
                    # and explicitly flag as estimated/unavailable to exclude from spatial logic
                    bbox = full_page_bbox
                    bbox_estimated = True
                    location_available = False

                elem = OCRElement(
                    id=f"lightonocr-p{page_number}-{idx}",
                    text=line_text,
                    bbox=bbox,
                    confidence=confidence,
                    page_number=page_number,
                    line_number=idx + 1,
                    source="lightonocr",
                    needs_vlm=needs_vlm,
                    metadata={
                        "bbox_estimated": bbox_estimated,
                        "location_available": location_available
                    }
                )
                elements.append(elem)

        if not elements and text:
            elements.append(
                OCRElement(
                    id=f"lightonocr-p{page_number}-full",
                    text=text,
                    bbox=[0.0, 0.0, float(image_width), float(image_height)],
                    confidence=confidence,
                    page_number=page_number,
                    source="lightonocr",
                    needs_vlm=needs_vlm,
                    metadata={
                        "bbox_estimated": True,
                        "location_available": False
                    }
                )
            )

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
