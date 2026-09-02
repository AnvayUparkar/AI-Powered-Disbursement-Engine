from pydantic import BaseModel


class DoclingOptions(BaseModel):
    """Configuration options for Docling document converter."""
    table_mode: str = "ACCURATE"  # 'ACCURATE' or 'FAST' (Node 2 requires ACCURATE)
    do_ocr: bool = False          # RapidOCR PP-OCRv6 runs in parallel/subsequent step
    do_table_structure: bool = True
    max_num_pages: int = 100
    images_scale: float = 2.0
