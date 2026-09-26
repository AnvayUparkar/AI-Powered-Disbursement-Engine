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
    kv_needs_review_count: int = 0
    kv_unmatched_label_count: int = 0
    lightonocr_processing_time: float = 0.0
    lightonocr_pages_processed: int = 0
    lightonocr_pages_failed: int = 0
    average_confidence: float = 0.0
    # Seconds per processing stage, in the order they ran (download, preprocess, scan_cleanup,
    # docling | lightonocr, page_images, vlm, comb_grid, serialize, llm_field_extraction, ...).
    stage_timings: Dict[str, float] = Field(default_factory=dict)
    # Docling's own per-model totals from conv_result.timings (layout, ocr, table_structure, ...),
    # summed across pages; stages overlap in Docling's threaded pipeline.
    docling_model_timings: Dict[str, float] = Field(default_factory=dict)


class ProcessingMetadata(BaseModel):
    """Metadata regarding how Node 2 processed the document."""
    document_id: str
    processing_id: str
    file_type: str
    mime_type: str
    file_size_bytes: int
    page_count: int
    docling_used: bool = True
    ocr_engine: str = "docling_ocr"
    ocr_model: str = "PP-OCRv6_medium"
    vlm_used: bool = False
    vlm_provider: Optional[str] = None
    metrics: ProcessingMetrics = Field(default_factory=ProcessingMetrics)
    custom_metadata: Dict[str, Any] = Field(default_factory=dict)
