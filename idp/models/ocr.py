from typing import List, Optional, Literal
from pydantic import BaseModel, Field


class BoundingBox(BaseModel):
    """Bounding box coordinates [x1, y1, x2, y2] normalized or absolute."""
    l: float = Field(description="Left / Min X")
    t: float = Field(description="Top / Min Y")
    r: float = Field(description="Right / Max X")
    b: float = Field(description="Bottom / Max Y")
    coord_origin: Literal["TOPLEFT", "BOTTOMLEFT"] = "TOPLEFT"

    def to_list(self) -> List[float]:
        return [self.l, self.t, self.r, self.b]

    def to_corners(self) -> List[List[float]]:
        return [
            [self.l, self.t],
            [self.r, self.t],
            [self.r, self.b],
            [self.l, self.b]
        ]


class OCRElement(BaseModel):
    """Individual OCR extracted text element with box, confidence, and provenance."""
    id: Optional[str] = None
    text: str
    bbox: List[float]  # [l, t, r, b]
    polygon: Optional[List[List[float]]] = None  # [[x1, y1], [x2, y2], [x3, y3], [x4, y4]]
    confidence: float
    page_number: int
    line_number: Optional[int] = None
    source: Literal["ocr", "vlm", "docling", "xml", "rapidocr", "vlm_corrected"] = "rapidocr"
    ocr_original: Optional[str] = None  # Preserved if VLM modified the text
    verified: bool = True
    needs_vlm: bool = False
    metadata: dict = Field(default_factory=dict)



class OCRResult(BaseModel):
    """Aggregate OCR result for a single page or document."""
    page_number: int
    elements: List[OCRElement] = []
    average_confidence: float = 0.0
    low_confidence_count: int = 0
    total_elements: int = 0
    rotation_applied: bool = False
    rotation_angle: float = 0.0
    # Actual rendered image pixel dimensions (set by DocumentProcessor after render)
    image_width: float = 0.0
    image_height: float = 0.0

