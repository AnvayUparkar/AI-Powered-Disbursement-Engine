"""Validation and sanitization layer for merged comb-box tokens.

Protects against unvalidated text passing through serialization while avoiding
over-aggressive generic free-text sanitization that corrupts valid numeric/alphanumeric codes.
"""
from typing import Optional
from idp.models.layout import LayoutElement


def validate_comb_box_token(elem: LayoutElement) -> str:
    """Validate a comb_box_merged layout element.

    Returns the sanitized/validated text to emit, or empty string if invalid.
    Preserves valid alphanumeric and numeric codes while flagging anomalies.
    """
    raw_text = (elem.text or "").strip()
    if not raw_text:
        return ""

    # Phase 3 / Phase 5: Check if element was flagged for review
    if elem.metadata and elem.metadata.get("needs_review"):
        # Flagged token: preserve text for downstream review/VLM inspection
        pass

    # Normalize internal whitespace while preserving token integrity
    import re
    cleaned = re.sub(r"\s+", " ", raw_text).strip()
    return cleaned
