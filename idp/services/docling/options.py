from typing import Optional, List
from pydantic import BaseModel


class DoclingOptions(BaseModel):
    """Configuration options for Docling document converter."""
    table_mode: str = "ACCURATE"  # 'ACCURATE' or 'FAST' (Node 2 requires ACCURATE)
    do_ocr: bool = True           # Docling-managed OCR engine (RapidOCR PP-OCRv6)
    do_table_structure: bool = True
    ocr_engine_name: str = "rapidocr"
    ocr_model_name: str = " PP-OCRv6_medium"
    det_model_path: Optional[str] = None
    rec_model_path: Optional[str] = None
    ocr_lang: List[str] = ["en", "hi", "devanagari", "english"]
    max_num_pages: int = 100
    images_scale: float = 2.0

