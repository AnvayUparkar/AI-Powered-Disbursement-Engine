import os
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from idp.utils.file_utils import detect_file_type, validate_file_size
from idp.core.exceptions import InvalidDocument
from idp.core.logging import logger, format_doc_log


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
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DocumentPreprocessor:
    """Preprocesses raw documents: validates format/size, determines page count & scan type."""

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

        if category == "pdf":
            page_count, is_scanned, dimensions = self._inspect_pdf(file_path, doc_id)
        elif category == "image":
            page_count, dimensions = self._inspect_image(file_path, doc_id)
            is_scanned = True
        elif category == "xml":
            page_count = 1
            dimensions = [{"width": 800.0, "height": 1100.0}]

        logger.info(format_doc_log(doc_id, f"Preprocessed {filename}: category={category}, pages={page_count}, scanned={is_scanned}"))

        return PreprocessedDocument(
            file_path=file_path,
            filename=filename,
            file_category=category,
            mime_type=mime_type,
            file_size_bytes=file_size,
            page_count=page_count,
            is_scanned_pdf=is_scanned,
            pages_dimensions=dimensions,
            metadata={"doc_id": doc_id}
        )

    def _inspect_pdf(self, file_path: str, doc_id: str) -> Tuple[int, bool, List[Dict[str, float]]]:
        page_count = 1
        is_scanned = False
        dimensions = []

        try:
            import fitz  # PyMuPDF
            doc = fitz.open(file_path)
            page_count = len(doc)
            total_text_chars = 0
            for page in doc:
                rect = page.rect
                dimensions.append({"width": float(rect.width), "height": float(rect.height)})
                total_text_chars += len(page.get_text().strip())
            doc.close()

            # If average text per page < 50 chars, treat as scanned PDF
            if page_count > 0 and (total_text_chars / page_count) < 50:
                is_scanned = True

        except Exception as e:
            logger.warning(format_doc_log(doc_id, f"PDF inspection fallback triggered: {e}"))
            page_count = 1
            is_scanned = True
            dimensions = [{"width": 595.0, "height": 842.0}]

        return page_count, is_scanned, dimensions

    def _inspect_image(self, file_path: str, doc_id: str) -> Tuple[int, List[Dict[str, float]]]:
        dimensions = []
        try:
            from PIL import Image
            with Image.open(file_path) as img:
                w, h = img.size
                dimensions.append({"width": float(w), "height": float(h)})
        except Exception:
            dimensions.append({"width": 1000.0, "height": 1000.0})

        return 1, dimensions
