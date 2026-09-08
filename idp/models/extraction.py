"""Models for extracted field spatial localization and bounding box debugging."""
from typing import List, Optional, Any, Literal
from pydantic import BaseModel, Field


class CandidateMatch(BaseModel):
    """Candidate OCR token match evaluation."""
    text: str
    page: int
    bbox: List[float]  # [x1, y1, x2, y2]
    score: float
    exactness: float
    ocr_confidence: float
    match_strategy: str
    constituent_tokens: List[str] = Field(default_factory=list)


class FieldLocation(BaseModel):
    """Spatial coordinate mapping of an extracted field back to OCR tokens."""
    field_name: str
    value: Any
    page: int = 1
    bbox: Optional[List[float]] = None  # [x1, y1, x2, y2] normalized (0.0 to 1.0)
    bbox_pixels: Optional[List[float]] = None  # [x1, y1, x2, y2] in original image/page pixels
    matched_text: Optional[str] = None
    confidence: float = 1.0  # OCR confidence
    match_confidence: float = 1.0  # Matching score
    location_status: Literal["resolved", "unresolved"] = "resolved"
    reason: Optional[str] = None
    source: str = "ocr"
    match_strategy: Optional[str] = None
    candidates: List[CandidateMatch] = Field(default_factory=list)


class OCRTokenDebug(BaseModel):
    """Debug representation of an individual OCR token."""
    id: str
    text: str
    page: int
    bbox: List[float]  # [x1, y1, x2, y2] normalized
    bbox_pixels: Optional[List[float]] = None
    confidence: float = 1.0
    source: str = "rapidocr"
    line_number: Optional[int] = None
