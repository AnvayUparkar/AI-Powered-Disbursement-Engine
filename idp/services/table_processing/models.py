from typing import List, Dict, Any, Optional
from enum import Enum
from pydantic import BaseModel, Field


class RowType(str, Enum):
    HEADER = "header"
    DATA = "data"
    SECTION_HEADER = "section_header"
    SUMMARY = "summary"
    KEY_VALUE = "key_value"


class LogicalBlockType(str, Enum):
    KEY_VALUE_TABLE = "key_value_table"
    STRUCTURED_TABLE = "structured_table"
    SECTION = "section"


class RawCell(BaseModel):
    """Raw cell representation within a physical table."""
    text: str = ""
    column_index: int = 0
    confidence: float = 1.0
    bbox: Optional[List[float]] = None


class RawRow(BaseModel):
    """Raw row of cells in a physical table."""
    cells: List[RawCell] = Field(default_factory=list)
    row_index: int = 0
    row_type: RowType = RowType.DATA


class PhysicalTable(BaseModel):
    """Physical table representing raw extracted grid before logical segmentation."""
    id: str
    page_number: int = 1
    rows: List[RawRow] = Field(default_factory=list)
    bbox: Optional[List[float]] = None


class LogicalBlock(BaseModel):
    """Segmented logical table block: either structured multi-column or key-value."""
    id: str
    page_number: int = 1
    page_numbers: List[int] = Field(default_factory=list)
    block_type: LogicalBlockType
    title: Optional[str] = None
    headers: List[str] = Field(default_factory=list)
    rows: List[List[str]] = Field(default_factory=list)
    structured_rows: List[Dict[str, str]] = Field(default_factory=list)
    summary_rows: List[Dict[str, Any]] = Field(default_factory=list)
    items: List[Dict[str, str]] = Field(default_factory=list)
    bbox: Optional[List[float]] = None

    def model_post_init(self, __context: Any) -> None:
        if not self.page_numbers and self.page_number:
            self.page_numbers = [self.page_number]
