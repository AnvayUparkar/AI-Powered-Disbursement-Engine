"""Data models and type definitions for the Document Registry."""
from typing import Any, Dict, List, Optional, TypedDict


class ExtractedFieldRecord(TypedDict, total=False):
    id: str
    name: str
    value: str
    confidence: float
    sourceDocumentId: str
    page: int
    type: str
    source: Optional[str]
    bbox: Optional[List[float]]
    locationStatus: Optional[str]
    matchedText: Optional[str]
    matchConfidence: Optional[float]
    reason: Optional[str]
    matchStrategy: Optional[str]
    candidates: Optional[List[Dict[str, Any]]]
    headers: Optional[List[str]]
    rows: Optional[List[List[str]]]


class ProcessingStepRecord(TypedDict, total=False):
    id: str
    component: str
    status: str
    detail: str
    startedAt: str
    confidence: Optional[float]


class DocumentDebugRecord(TypedDict, total=False):
    field_locations: Dict[str, Any]
    ocr_tokens: List[Dict[str, Any]]
    page_dimensions: List[Dict[str, Any]]


class DocumentRecord(TypedDict, total=False):
    id: str
    name: str
    type: str
    pages: int
    ocrStatus: str
    extractionStatus: str
    confidence: float
    vlmUsed: bool
    uploadedAt: str
    caseId: str
    sizeKb: int
    extractedFields: List[Dict[str, Any]]
    processingSteps: List[Dict[str, Any]]
    # Which OCR engine produced the text (e.g. {"engine": "lightonocr", "label": "LightOnOCR via LiteLLM", ...});
    # None when unknown. Built by registry.normalizer.build_ocr_engine_info.
    ocrEngine: Optional[Dict[str, Any]]
    # Per-stage seconds from the idp pod ({stages: [{stage, seconds}], totalSeconds, doclingModels}); None if not recorded.
    timing: Optional[Dict[str, Any]]
    rawText: str
    formattedText: str
    debug: Dict[str, Any]
