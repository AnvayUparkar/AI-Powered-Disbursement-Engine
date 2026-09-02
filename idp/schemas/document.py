from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class ProcessDocumentRequest(BaseModel):
    """API Request payload for triggering document processing."""
    document_id: str = Field(..., description="Unique ID of the document")
    s3_key: str = Field(..., description="S3 Key location of the raw document e.g. raw-documents/DOC123.pdf")
    s3_bucket: Optional[str] = Field(None, description="Optional S3 bucket override")


class DocumentStatusResponse(BaseModel):
    """Status response returned to API caller."""
    document_id: str
    processing_id: str
    status: str  # 'completed', 'failed', 'processing'
    output_location: str
    processing_time_seconds: float
    error_message: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
