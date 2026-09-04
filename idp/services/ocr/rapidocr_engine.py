import uuid
from typing import Any, List, Union, Optional

from idp.models.ocr import OCRElement, OCRResult
from idp.services.ocr.preprocessing import OCRImagePreprocessor
from idp.services.ocr.confidence import OCRConfidenceEvaluator
from idp.core.logging import logger, format_doc_log


_shared_rapid_ocr: Optional[Any] = None


class RapidOCREngine:
    """RapidOCR PP-OCRv6 engine implementation preserving polygon coordinates and confidence."""

    def __init__(self):
        self.preprocessor = OCRImagePreprocessor()
        self.evaluator = OCRConfidenceEvaluator()
        self._rapid_ocr = None

    def _get_engine(self):
        global _shared_rapid_ocr
        if _shared_rapid_ocr is not None:
            return _shared_rapid_ocr

        if self._rapid_ocr is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
                self._rapid_ocr = RapidOCR()
                _shared_rapid_ocr = self._rapid_ocr
                logger.info("RapidOCR PP-OCRv6 engine initialized successfully.")
            except Exception as e:
                logger.warning(f"RapidOCR initialization note/fallback: {e}. RapidOCR library not imported or mock active.")
                self._rapid_ocr = "MOCK"
                _shared_rapid_ocr = "MOCK"
        return self._rapid_ocr


    def process(
        self,
        image_input: Union[str, bytes],
        page_number: int = 1,
        doc_id: str = "DOC",
        skip_preprocessing: bool = False
    ) -> OCRResult:
        """
        Run PP-OCRv6 text detection & recognition on image file or bytes.

        Returns:
            OCRResult object with extracted text elements, polygons, bboxes, and confidence metrics.
        """
        logger.info(format_doc_log(doc_id, f"Running RapidOCR PP-OCRv6 on page {page_number}"))

        # Convert path to bytes if file path passed
        image_bytes = b""
        if isinstance(image_input, str):
            with open(image_input, "rb") as f:
                image_bytes = f.read()
        else:
            image_bytes = image_input

        # Apply image preprocessing (deskew, contrast, rotation metadata)
        processed_bytes, prep_meta = self.preprocessor.preprocess_image(
            image_bytes, doc_id=doc_id, skip_preprocessing=skip_preprocessing
        )

        engine = self._get_engine()
        elements: List[OCRElement] = []

        if engine == "MOCK":
            return self._fallback_ocr(processed_bytes, page_number, prep_meta, doc_id)

        try:
            import numpy as np
            import cv2

            nparr = np.frombuffer(processed_bytes, np.uint8)
            img_np = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            if img_np is not None:
                raw_res = engine(img_np)
            else:
                raw_res = engine(processed_bytes)

            boxes = []
            txts = []
            scores = []

            # Format 1: rapidocr 3.9+ RapidOCROutput dataclass
            if hasattr(raw_res, 'boxes') and hasattr(raw_res, 'txts') and hasattr(raw_res, 'scores'):
                boxes = raw_res.boxes if raw_res.boxes is not None else []
                txts = raw_res.txts if raw_res.txts is not None else []
                scores = raw_res.scores if raw_res.scores is not None else []
            # Format 2: rapidocr_onnxruntime list/tuple format
            elif isinstance(raw_res, (list, tuple)):
                items = raw_res[0] if (len(raw_res) == 2 and isinstance(raw_res[0], (list, tuple))) else raw_res
                for item in (items or []):
                    if len(item) >= 3:
                        boxes.append(item[0])
                        txts.append(item[1])
                        scores.append(item[2])

            for line_idx, (polygon, text, score) in enumerate(zip(boxes, txts, scores)):
                polygon_list = polygon.tolist() if hasattr(polygon, 'tolist') else polygon
                xs = [pt[0] for pt in polygon_list]
                ys = [pt[1] for pt in polygon_list]
                bbox = [float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))]

                elem = OCRElement(
                    id=f"ocr-{uuid.uuid4().hex[:8]}",
                    text=str(text).strip(),
                    bbox=bbox,
                    polygon=polygon_list,
                    confidence=float(score),
                    page_number=page_number,
                    line_number=line_idx + 1,
                    source="ocr"
                )
                elements.append(elem)


            ocr_res = OCRResult(
                page_number=page_number,
                elements=elements,
                rotation_applied=prep_meta.get("rotation_applied", False),
                rotation_angle=prep_meta.get("rotation_angle", 0.0)
            )

            ocr_res = self.evaluator.evaluate_result(ocr_res)
            logger.info(format_doc_log(doc_id, f"OCR Page {page_number}: {len(elements)} elements, avg_conf={ocr_res.average_confidence:.2f}, low_conf_count={ocr_res.low_confidence_count}"))
            return ocr_res

        except Exception as e:
            logger.error(format_doc_log(doc_id, f"RapidOCR execution failure: {e}"))
            return self._fallback_ocr(processed_bytes, page_number, prep_meta, doc_id)

    def _fallback_ocr(
        self,
        image_bytes: bytes,
        page_number: int,
        prep_meta: dict,
        doc_id: str
    ) -> OCRResult:
        """Fallback text extractor when ONNX runtime is absent or fails."""
        logger.info(format_doc_log(doc_id, f"Executing fallback OCR for page {page_number}"))
        elements: List[OCRElement] = []

        try:
            import fitz
            doc = fitz.open(stream=image_bytes, filetype="png")
            for pidx, page in enumerate(doc):
                blocks = page.get_text("blocks")
                for bno, b in enumerate(blocks):
                    text = b[4].strip() if len(b) > 4 else ""
                    if text:
                        elements.append(
                            OCRElement(
                                id=f"ocr-fb-{bno + 1}",
                                text=text,
                                bbox=[float(b[0]), float(b[1]), float(b[2]), float(b[3])],
                                polygon=[[float(b[0]), float(b[1])], [float(b[2]), float(b[1])], [float(b[2]), float(b[3])], [float(b[0]), float(b[3])]],
                                confidence=0.90,
                                page_number=page_number,
                                line_number=bno + 1,
                                source="ocr"
                            )
                        )
            doc.close()
        except Exception:
            pass

        res = OCRResult(
            page_number=page_number,
            elements=elements,
            rotation_applied=prep_meta.get("rotation_applied", False),
            rotation_angle=prep_meta.get("rotation_angle", 0.0)
        )
        return self.evaluator.evaluate_result(res)
