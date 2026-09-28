"""Document-type-aware system prompt dispatcher.

Usage
-----
    from pipeline.engines.prompts import get_system_prompt

    system_prompt = get_system_prompt(doc_type)   # doc_type is a canonical key

A type with a template in pipeline/engines/doc_templates/<doc_type>.yaml gets its
prompt generated from that template (currently: sanction_letter, kfs, application_form).

Every other type falls back to the universal key-value extraction prompt in misc.py,
which extracts all key-value pairs plus the canonical keys the LOS checks read.
"""

from __future__ import annotations

from pipeline.engines.doc_templates import build_system_prompt, get_doc_template
from pipeline.engines.prompts import misc

_FALLBACK_PROMPT: str = misc.SYSTEM_PROMPT


def get_system_prompt(doc_type: str) -> str:
    """Return the system prompt appropriate for *doc_type*.

    Args:
        doc_type: Canonical document type key as returned by
                  ``config.doc_types.get_canonical_doc_type()``.
                  Examples: ``"aadhaar"``, ``"kfs"``, ``"sanction_letter"``.

    Returns:
        The template-generated prompt when the type has a template, otherwise the
        universal key-value extraction prompt.
    """
    template = get_doc_template(doc_type)
    if template is not None:
        return build_system_prompt(template)
    return _FALLBACK_PROMPT
