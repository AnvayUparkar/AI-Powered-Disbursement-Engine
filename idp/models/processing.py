from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class ProcessingMetrics(BaseModel):
    """Execution timing and processing count metrics for Node 2."""
    docling_processing_time: float = 0.0
    ocr_processing_time: float = 0.0
    vlm_processing_time: float = 0.0
    total_processing_time: float = 0.0
    vlm_fallback_count: int = 0
    ocr_low_confidence_count: int = 0
    total_elements_extracted: int = 0


class ProcessingMetadata(BaseModel):
    """Metadata regarding how Node 2 processed the document."""
    document_id: str
    processing_id: str
    file_type: str
    mime_type: str
    file_size_bytes: int
    page_count: int
    docling_used: bool = True
    ocr_engine: str = "rapidocr"
    ocr_model: str = "PP-OCRv6"
    vlm_used: bool = False
    vlm_provider: Optional[str] = None
    metrics: ProcessingMetrics = Field(default_factory=ProcessingMetrics)
    custom_metadata: Dict[str, Any] = Field(default_factory=dict)
