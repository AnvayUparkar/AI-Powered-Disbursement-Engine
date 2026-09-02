from typing import Any, Dict, Optional
from pydantic import BaseModel


class HealthCheckResponse(BaseModel):
    status: str = "ok"
    app_name: str
    environment: str
    ocr_engine: str
    vlm_enabled: bool


class ErrorResponse(BaseModel):
    error: str
    message: str
    details: Optional[str] = None
    document_id: Optional[str] = None
