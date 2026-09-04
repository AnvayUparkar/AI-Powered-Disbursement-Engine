from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from idp.models.layout import LayoutElement
from idp.models.table import TableStructure
from idp.models.processing import ProcessingMetadata


class PageInformation(BaseModel):
    """Page dimension and element layout details."""
    page_number: int
    width: float = 0.0
    height: float = 0.0
    elements: List[LayoutElement] = Field(default_factory=list)
    tables: List[TableStructure] = Field(default_factory=list)


class DocumentSource(BaseModel):
    """Source file provenance metadata."""
    filename: str
    mime_type: str
    s3_bucket: Optional[str] = None
    s3_key: Optional[str] = None


class ParsedDocument(BaseModel):
    """Canonical Unified Document Representation (Contract between Node 2 and Node 3)."""
    document_id: str
    source: DocumentSource
    pages: List[PageInformation] = Field(default_factory=list)
    tables: List[TableStructure] = Field(default_factory=list)
    elements: List[LayoutElement] = Field(default_factory=list)
    text: str = ""
    raw_text: Optional[str] = None
    formatted_text: Optional[str] = None
    processing: ProcessingMetadata
    custom_metadata: Dict[str, Any] = Field(default_factory=dict)

