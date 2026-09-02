from enum import Enum
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field


class ElementType(str, Enum):
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    TEXT = "text"
    TITLE = "title"
    FOOTER = "footer"
    HEADER = "header"
    CAPTION = "caption"
    LIST_ITEM = "list_item"
    TABLE = "table"
    KEY_VALUE = "key_value"
    IMAGE = "image"
    UNKNOWN = "unknown"


class LayoutElement(BaseModel):
    """Layout block element parsed from Docling or OCR."""
    id: str
    type: ElementType = ElementType.TEXT
    text: str
    bbox: List[float] = Field(default_factory=list)  # [l, t, r, b]
    confidence: float = 1.0
    page_number: int
    reading_order: Optional[int] = None
    level: Optional[int] = None  # Heading level if applicable
    source: str = "rapidocr"
    structure_source: str = "none"
    ocr_original: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

