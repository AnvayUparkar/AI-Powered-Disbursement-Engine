import os
from typing import Dict, Any, List, Optional, Tuple
from pydantic import BaseModel, Field
from idp.utils.file_utils import detect_file_type, validate_file_size
from idp.core.exceptions import InvalidDocument
from idp.core.logging import logger, format_doc_log


from idp.services.scanned_quality_gate import ScannedDocQualityGate, ScannedQualityReport


class PreprocessedDocument(BaseModel):
    """Result of DocumentPreprocessor inspection and normalization."""
    file_path: str
    filename: str
    file_category: str  # 'pdf', 'image', 'xml'
    mime_type: str
    file_size_bytes: int
    page_count: int
    is_scanned_pdf: bool = False
    pages_dimensions: List[Dict[str, float]] = Field(default_factory=list)  # [{'width': W, 'height': H}]
    pages_quality: List[ScannedQualityReport] = Field(default_factory=list)
    overall_scan_grade: str = "CLEAN"  # 'CLEAN', 'MIXED', 'DEGRADED'
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DocumentPreprocessor:
    """Preprocesses raw documents: validates format/size, determines page count & scan type."""

    def __init__(self, quality_gate: Optional[ScannedDocQualityGate] = None) -> None:
        self.quality_gate = quality_gate or ScannedDocQualityGate()

    def preprocess(self, file_path: str, doc_id: str = "DOC") -> PreprocessedDocument:
        logger.info(format_doc_log(doc_id, f"Preprocessing document: {file_path}"))

        if not os.path.exists(file_path):
            raise InvalidDocument(f"File not found: {file_path}")

        filename = os.path.basename(file_path)
        file_size = validate_file_size(file_path)
        category, mime_type = detect_file_type(file_path)

        page_count = 1
        is_scanned = False
        dimensions = []
        pages_quality: List[ScannedQualityReport] = []
        overall_scan_grade = "CLEAN"

        if category == "pdf":
            page_count, is_scanned, dimensions, pages_quality, overall_scan_grade = self._inspect_pdf(file_path, doc_id)
        elif category == "image":
            page_count, dimensions, pages_quality, overall_scan_grade = self._inspect_image(file_path, doc_id)
            is_scanned = True
        elif category == "xml":
            page_count = 1
            dimensions = [{"width": 800.0, "height": 1100.0}]

        logger.info(format_doc_log(doc_id, f"Preprocessed {filename}: category={category}, pages={page_count}, scanned={is_scanned}, grade={overall_scan_grade}"))

        return PreprocessedDocument(
            file_path=file_path,
            filename=filename,
            file_category=category,
            mime_type=mime_type,
            file_size_bytes=file_size,
            page_count=page_count,
            is_scanned_pdf=is_scanned,
            pages_dimensions=dimensions,
            pages_quality=pages_quality,
            overall_scan_grade=overall_scan_grade,
            metadata={"doc_id": doc_id}
        )

    def _inspect_pdf(
        self, file_path: str, doc_id: str
    ) -> Tuple[int, bool, List[Dict[str, float]], List[ScannedQualityReport], str]:
        page_count = 1
        is_scanned = False
        dimensions = []
        pages_quality: List[ScannedQualityReport] = []
        overall_scan_grade = "CLEAN"

        try:
            import fitz  # PyMuPDF
            import numpy as np
            import cv2
            doc = fitz.open(file_path)
            page_count = len(doc)
            total_text_chars = 0
            for page in doc:
                rect = page.rect
                dimensions.append({"width": float(rect.width), "height": float(rect.height)})
                total_text_chars += len(page.get_text().strip())

            # If average text per page < 50 chars, treat as scanned PDF
            if page_count > 0 and (total_text_chars / page_count) < 50:
                is_scanned = True
                clean_count = 0
                degraded_count = 0

                for idx, page in enumerate(doc):
                    eff_dpi = self.quality_gate.calculate_effective_dpi(page)
                    # Fast inspection pixmap (~100 DPI for ultra-fast heuristics)
                    scale = 100.0 / 72.0
                    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
                    img = cv2.cvtColor(
                        np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n),
                        cv2.COLOR_RGB2BGR
                    )
                    report = self.quality_gate.analyze_scanned_image(img, effective_dpi=eff_dpi, page_number=idx + 1)
                    pages_quality.append(report)

                    if report.quality_grade == "CLEAN":
                        clean_count += 1
                    else:
                        degraded_count += 1

                if clean_count == len(pages_quality):
                    overall_scan_grade = "CLEAN"
                elif degraded_count == len(pages_quality):
                    overall_scan_grade = "DEGRADED"
                else:
                    overall_scan_grade = "MIXED"

            doc.close()

        except Exception as e:
            logger.warning(format_doc_log(doc_id, f"PDF inspection fallback triggered: {e}"))
            if page_count < 1:
                page_count = 1
            if not dimensions:
                dimensions = [{"width": 595.0, "height": 842.0}] * page_count
            is_scanned = True

        return page_count, is_scanned, dimensions, pages_quality, overall_scan_grade

    def _inspect_image(
        self, file_path: str, doc_id: str
    ) -> Tuple[int, List[Dict[str, float]], List[ScannedQualityReport], str]:
        dimensions = []
        pages_quality: List[ScannedQualityReport] = []
        overall_scan_grade = "CLEAN"

        try:
            import cv2
            img = cv2.imread(file_path)
            if img is not None:
                h, w = img.shape[:2]
                dimensions.append({"width": float(w), "height": float(h)})
                # Estimate baseline DPI assuming standard document height ~11 inches
                est_dpi = int(max(w, h) / 11.0)
                report = self.quality_gate.analyze_scanned_image(img, effective_dpi=est_dpi, page_number=1)
                pages_quality.append(report)
                overall_scan_grade = report.quality_grade
            else:
                dimensions.append({"width": 1000.0, "height": 1000.0})
        except Exception:
            dimensions.append({"width": 1000.0, "height": 1000.0})

        return 1, dimensions, pages_quality, overall_scan_grade
