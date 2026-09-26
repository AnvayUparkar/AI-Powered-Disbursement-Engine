"""Document-type-aware system prompt dispatcher.

Usage
-----
    from pipeline.engines.prompts import get_system_prompt

    system_prompt = get_system_prompt(doc_type)   # doc_type is a canonical key

Supported canonical types (each has a dedicated, schema-constrained prompt):
    - application_form
    - kfs
    - sanction_letter
    - aadhaar
    - pan

All other types (including 'misc', 'miscellaneous', and any unknown/unresolved
key) fall back to the universal key-value extraction prompt in misc.py.
"""

from __future__ import annotations

from pipeline.engines.prompts import (
    aadhaar,
    application_form,
    kfs,
    misc,
    pan,
    sanction_letter,
)

# Mapping from canonical doc-type key → module that contains SYSTEM_PROMPT
_PROMPT_MAP: dict[str, str] = {
    "aadhaar":          aadhaar.SYSTEM_PROMPT,
    "pan":              pan.SYSTEM_PROMPT,
    "application_form": application_form.SYSTEM_PROMPT,
    "kfs":              kfs.SYSTEM_PROMPT,
    "sanction_letter":  sanction_letter.SYSTEM_PROMPT,
}

_FALLBACK_PROMPT: str = misc.SYSTEM_PROMPT


def get_system_prompt(doc_type: str) -> str:
    """Return the system prompt appropriate for *doc_type*.

    Args:
        doc_type: Canonical document type key as returned by
                  ``config.doc_types.get_canonical_doc_type()``.
                  Examples: ``"aadhaar"``, ``"kfs"``, ``"sanction_letter"``.

    Returns:
        A system prompt string tailored to the document type.
        Falls back to the universal misc prompt for any unknown or
        miscellaneous type.
    """
    return _PROMPT_MAP.get((doc_type or "").lower().strip(), _FALLBACK_PROMPT)
