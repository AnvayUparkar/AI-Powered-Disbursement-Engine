from typing import List, Optional
from pydantic import BaseModel


class OCRRegionRequest(BaseModel):
    """Payload for isolated region OCR or VLM check."""
    document_id: str
    page_number: int
    bbox: List[float]
    ocr_text: Optional[str] = None
    ocr_confidence: Optional[float] = None
