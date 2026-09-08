"""Shim for backward compatibility — re-exports from pipeline.engines.llm_field_extractor."""
import httpx
from pipeline.engines.llm_field_extractor import *
from pipeline.engines.llm_field_extractor import (
    TEMPLATE_FIELDS,
    _CANONICAL_KEYS,
    _SYSTEM_PROMPT,
    _build_user_content,
    _clean_json_response,
    format_template_json,
    llm_extract_fields,
)

