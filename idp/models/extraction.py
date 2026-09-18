"""Models for extracted field spatial localization and bounding box debugging."""
from typing import List, Optional, Any, Literal
from pydantic import BaseModel, Field


# Canonical fields that are DERIVED rather than read off the page. They are computed from
# document presence/signature checks, never appear as text, and therefore can never resolve
# to a bounding box. Reported as "not_locatable" rather than as a location failure.
DERIVED_FLAG_FIELDS: frozenset[str] = frozenset({
    "aadhaar_xml_present",
    "loan_agreement_present",
    "loan_agreement_signed",
})


class CandidateMatch(BaseModel):
    """Candidate OCR token match evaluation."""
    text: str
    page: int
    bbox: List[float]  # [x1, y1, x2, y2]
    score: float
    exactness: float
    ocr_confidence: float
    match_strategy: str
    source: str = "docling_ocr"
    constituent_tokens: List[str] = Field(default_factory=list)


class FieldLocation(BaseModel):
    """Spatial coordinate mapping of an extracted field back to OCR tokens."""
    field_name: str
    value: Any
    page: int = 1
    bbox: Optional[List[float]] = None  # [x1, y1, x2, y2] normalized (0.0 to 1.0)
    bbox_pixels: Optional[List[float]] = None  # [x1, y1, x2, y2] in original image/page pixels
    matched_text: Optional[str] = None
    # NOTE: these two default to None, NOT 1.0. Only the "resolved" branch of
    # FieldLocationResolver sets them; every non-located status (unresolved,
    # not_extracted, not_locatable, no_ocr_text) leaves them unset. A float default
    # of 1.0 meant those fields rendered as "100%" and "Match: 100%" in the review
    # UI -- a fabricated perfect score on a value that was never matched to anything,
    # sitting right next to an honest "OCR n/a / Layout n/a". Unknown must read as
    # unknown.
    confidence: Optional[float] = None  # OCR confidence (mirrors ocr_confidence, kept for compatibility)
    ocr_confidence: Optional[float] = None  # RapidOCR recognition score of the matched token
    layout_confidence: Optional[float] = None  # Layout model score of the region it sits in
    match_confidence: Optional[float] = None  # Matching score
    location_status: Literal[
        "resolved",        # matched to a token/cell on the page
        "unresolved",      # a real value that could not be matched to the page
        "not_extracted",   # no value was extracted, so there is nothing to locate
        "not_locatable",   # derived flag; never appears as text on the page
        "no_ocr_text",     # the document produced no OCR tokens or table cells at all
    ] = "resolved"
    reason: Optional[str] = None
    source: str = "docling_ocr"
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
    # Real model-reported scores, carried separately so the debug overlay can show which
    # stage is actually uncertain (RapidOCR recognition vs the layout model's region call).
    ocr_confidence: Optional[float] = None
    layout_confidence: Optional[float] = None
    source: str = "docling_ocr"
    line_number: Optional[int] = None
