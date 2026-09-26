"""LLM Field Extractor — Raw OCR text → structured JSON via OpenRouter.

Replaces the regex rulebook in Node 2.  The raw OCR output (ParsedDocument.text)
is sent directly to an OpenRouter LLM with a universal field-extraction prompt.
The LLM does NOT know the document type; it receives a fixed list of canonical
field names and is instructed to return null for anything not explicitly present.

Large docs  (kfs, loan_agreement, sanction_letter, application_form, account_statement)
  → OCR text is written to a temp .md file; the file content is sent as the user message.
Small docs  (aadhaar, pan, disbursal_memo)
  → OCR text is sent inline in the user message.

The returned dict uses the exact canonical field names expected by
extract_field_value() in comparison_utils, matching NODE3A/3B/3C_FIELD_CHECKS.
"""

import json
import logging
import os
import re
import tempfile
from typing import Any

import httpx

from config.settings import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MAX_TOKENS,
    LLM_MODEL,
)

logger = logging.getLogger("disbursement_pipeline.llm_field_extractor")

# ── Document type classification ───────────────────────────────────────────
LARGE_DOC_TYPES: frozenset[str] = frozenset({
    "kfs",
    "loan_agreement",
    "sanction_letter",
    "application_form",
    "account_statement",
})

SMALL_DOC_TYPES: frozenset[str] = frozenset({
    "aadhaar",
    "pan",
    "disbursal_memo",
})

# Ordered canonical field names strictly matching the required JSON template schema.
TEMPLATE_FIELDS: tuple[str, ...] = (
    "applicant_name",
    "fathers_name",
    "dob",
    "mobile_no",
    "gender",
    "aadhaar_number",
    "pan_number",
    "address",
    "current_address",
    "bank_account_no",
    "type_of_account",
    "loan_amount",
    "loan_validity",
    "loan_type",
    "application_no",
    "application_date",
    "BPI",
    "irr_percent",
    "emi",
    "customer_consent",
)

_CANONICAL_KEYS: frozenset[str] = frozenset(TEMPLATE_FIELDS)


def format_template_json(extracted: dict[str, Any] | None) -> dict[str, Any]:
    """Formats an arbitrary extracted dictionary into the exact 20-field canonical template.

    Keys are returned in the exact canonical order with non-present fields as None,
    and boolean flag (customer_consent) as False by default.
    """
    boolean_keys = {"customer_consent"}
    norm = dict(extracted or {})

    if norm.get("applicant_name") is None:
        for alias in ("customer_name", "borrower_name", "full_name", "name"):
            if norm.get(alias) is not None:
                norm["applicant_name"] = norm[alias]
                break
    if norm.get("bank_account_no") is None and "account_no" in norm:
        norm["bank_account_no"] = norm["account_no"]
    if norm.get("application_no") is None:
        for alias in ("loan_no", "loan_account_no", "application_id", "loan_id", "appl_no", "los_id"):
            if norm.get(alias) is not None:
                norm["application_no"] = norm[alias]
                break
    if norm.get("pan_number") is None and norm.get("pan") is not None:
        norm["pan_number"] = norm["pan"]
    if norm.get("aadhaar_number") is None and norm.get("aadhaar") is not None:
        norm["aadhaar_number"] = norm["aadhaar"]
    if norm.get("loan_amount") is None:
        for alias in ("sanctioned_amount", "funding_amount", "disbursal_amount", "requested_loan_amount"):
            if norm.get(alias) is not None:
                norm["loan_amount"] = norm[alias]
                break
    if norm.get("loan_validity") is None:
        for alias in ("tenure_months", "tenure", "tenor", "tenure_of_loan"):
            if norm.get(alias) is not None:
                norm["loan_validity"] = norm[alias]
                break
    if norm.get("loan_type") is None:
        for alias in ("type_of_loan", "end_use", "purpose_of_loan"):
            if norm.get(alias) is not None:
                norm["loan_type"] = norm[alias]
                break
    if norm.get("BPI") is None:
        for alias in ("bpi", "broken_period_interest"):
            if norm.get(alias) is not None:
                norm["BPI"] = norm[alias]
                break
    if norm.get("irr_percent") is None:
        for alias in ("roi", "interest_rate", "irr"):
            if norm.get(alias) is not None:
                norm["irr_percent"] = norm[alias]
                break
    if norm.get("customer_consent") is None:
        for alias in ("consent", "is_consented", "otp_consent", "borrower_consent", "customer_acceptance"):
            if norm.get(alias) is not None:
                norm["customer_consent"] = norm[alias]
                break
    if norm.get("address") is None and norm.get("address_text") is not None:
        norm["address"] = norm["address_text"]

    result: dict[str, Any] = {}
    for k in TEMPLATE_FIELDS:
        if k in boolean_keys:
            val = norm.get(k, False)
            result[k] = bool(val) if val is not None else False
        else:
            result[k] = norm.get(k, None)

    # Append all other extracted key-value pairs, ignoring internal metadata and duplicate aliases
    _skip_keys = {
        "rawText", "formattedText", "documentMarkdown", "document_markdown",
        "customer_name", "borrower_name", "full_name", "name", "account_no",
        "loan_no", "loan_account_no", "application_id", "loan_id", "appl_no", "los_id",
        "pan", "aadhaar", "sanctioned_amount", "funding_amount", "disbursal_amount",
        "requested_loan_amount", "tenure_months", "tenure", "tenor", "tenure_of_loan",
        "type_of_loan", "end_use", "purpose_of_loan", "bpi", "broken_period_interest",
        "roi", "interest_rate", "irr", "consent", "is_consented", "otp_consent",
        "borrower_consent", "customer_acceptance", "address_text",
    }
    for k, v in norm.items():
        if not k.startswith("_") and k not in result and k not in _skip_keys:
            result[k] = v

    return result

# ── Per-document-type system prompt dispatcher ────────────────────────────
# Prompts live in pipeline/engines/prompts/<doc_type>.py.
# get_system_prompt() returns the tailored schema prompt for each canonical
# type, falling back to the universal misc prompt for unknown types.
from pipeline.engines.prompts import get_system_prompt as _get_system_prompt



from pipeline.engines.llm_client import clean_json_response

_clean_json_response = clean_json_response


# ── Helpers ────────────────────────────────────────────────────────────────

def _build_user_content(doc_type: str, raw_text: str) -> str:
    """Returns the user-message content for the LLM.

    Large docs: OCR text is written to a temp .md file and read back so that any
    markdown formatting (headers, tables) produced by Docling is preserved.
    Small docs: raw_text is sent directly inline.
    """
    if doc_type in LARGE_DOC_TYPES:
        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".md",
                prefix=f"ocr_{doc_type}_",
                encoding="utf-8",
                delete=False,
            ) as tmp:
                tmp.write(raw_text)
                tmp_path = tmp.name

            with open(tmp_path, "r", encoding="utf-8") as f:
                content = f.read()

            return content
        except OSError as e:
            logger.warning(
                "Failed to write/read temp .md for %s: %s — falling back to inline text",
                doc_type,
                e,
            )
            return raw_text
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
    else:
        return raw_text


# ── Public API ─────────────────────────────────────────────────────────────

def llm_extract_fields(
    doc_type: str,
    raw_text: str,
    doc_id: str,
) -> dict[str, Any]:
    """Sends raw OCR text to the configured LLM and returns a structured field dict.

    Args:
        doc_type: Canonical document type key (e.g. ``"aadhaar"``, ``"kfs"``).
        raw_text: Raw OCR text from ``ParsedDocument.text``.
        doc_id:   Document ID used for structured logging.

    Returns:
        Dict mapping canonical field names -> extracted values (``None`` for
        fields not found in the document). Returns ``{}`` on any failure so
        the caller can proceed gracefully without a crash.
    """
    from pipeline.engines import llm_field_extractor
    effective_api_key = getattr(llm_field_extractor, "LLM_API_KEY", None)
    effective_model = getattr(llm_field_extractor, "LLM_MODEL", None)
    effective_base_url = getattr(llm_field_extractor, "LLM_BASE_URL", None)
    effective_temperature = getattr(llm_field_extractor, "LLM_TEMPERATURE", 0.0)

    if not effective_api_key:
        logger.warning("[%s] LLM_API_KEY not set — skipping LLM field extraction", doc_id)
        return {}

    if not raw_text or not raw_text.strip():
        logger.warning("[%s] Empty OCR text — skipping LLM field extraction", doc_id)
        return {}

    user_content = _build_user_content(doc_type, raw_text)

    system_prompt = _get_system_prompt(doc_type)
    logger.debug(
        "[%s] Using system prompt for doc_type=%r (prompt length=%d chars)",
        doc_id,
        doc_type,
        len(system_prompt),
    )

    try:
        from pipeline.engines.llm_client import invoke_llm_json
        extracted = invoke_llm_json(
            system_prompt=system_prompt,
            user_prompt=user_content,
            model=effective_model,
            api_key=effective_api_key,
            base_url=effective_base_url,
            temperature=effective_temperature,
            max_tokens=LLM_MAX_TOKENS,
            timeout=60.0,
            purpose="field_extraction",
        )
        if not extracted or not isinstance(extracted, dict):
            logger.warning("[%s] LLM extraction response empty or invalid (doc_type=%s)", doc_id, doc_type)
            return {}

        result: dict[str, Any] = format_template_json(extracted)
        non_null = sum(1 for v in result.values() if v is not None and v is not False)
        logger.info(
            "[%s] LLM extracted %d/%d non-null fields (doc_type=%s, model=%s)",
            doc_id,
            non_null,
            len(TEMPLATE_FIELDS),
            doc_type,
            LLM_MODEL,
        )
        return result

    except httpx.HTTPStatusError as e:
        logger.error(
            "[%s] LLM Provider HTTP %s (%s): %s",
            doc_id,
            e.response.status_code,
            effective_model,
            e.response.text[:500],
        )
    except httpx.TimeoutException:
        logger.error("[%s] LLM request timed out (doc_type=%s, model=%s)", doc_id, doc_type, effective_model)
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.error("[%s] Failed to parse LLM JSON response: %s", doc_id, e)
    except Exception as e:  # noqa: BLE001 — defensive boundary, always return {}
        logger.error("[%s] Unexpected error in LLM field extraction: %s", doc_id, e)

    return {}
