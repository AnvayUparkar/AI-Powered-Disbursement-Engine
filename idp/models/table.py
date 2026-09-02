from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field


class TableCell(BaseModel):
    """Individual table cell model."""
    row_index: int
    col_index: int
    row_span: int = 1
    col_span: int = 1
    text: str
    is_header: bool = False
    bbox: Optional[List[float]] = None
    confidence: float = 1.0


class TableStructure(BaseModel):
    """Structured representation of a document table."""
    id: str
    page_number: int
    num_rows: int
    num_cols: int
    cells: List[TableCell] = []
    bbox: Optional[List[float]] = None
    caption: Optional[str] = None
    csv_grid: Optional[str] = None
    headers: List[str] = Field(default_factory=list)
    rows_raw: List[List[str]] = Field(default_factory=list)
