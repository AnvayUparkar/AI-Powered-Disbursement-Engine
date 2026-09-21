"""LLM Field Extractor — Raw OCR text → structured JSON via OpenRouter / Gemini.

Replaces regex heuristics in IDP canonical extraction. The raw OCR output
(ParsedDocument.text) is sent directly to the configured LLM with a universal
field-extraction prompt. The LLM does NOT know the document type; it receives
a fixed list of canonical field names and is instructed to return null for
anything not explicitly present.

Large docs  (kfs, loan_agreement, sanction_letter, application_form, account_statement)
  → OCR text is written to a temp .md file; the file content is sent as the user message.
Small docs  (aadhaar, pan, disbursal_memo)
  → OCR text is sent inline in the user message.

The returned dict uses the exact canonical field names expected by the
downstream 20-field canonical template schema.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from typing import Any

import httpx

from config.doc_types import TEMPLATE_FIELDS, format_template_json
from config.settings import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MAX_TOKENS,
    LLM_MODEL,
    LLM_TEMPERATURE,
)
from idp.services.extraction.llm_client import clean_json_response, invoke_llm_json

logger = logging.getLogger("idp.services.extraction.llm_field_extractor")

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

_CANONICAL_KEYS: frozenset[str] = frozenset(TEMPLATE_FIELDS)
_clean_json_response = clean_json_response

# ── Universal extraction prompt ────────────────────────────────────────────
_SYSTEM_PROMPT: str = (
    "You are a financial document field extraction engine.\n"
    "Your task: extract specific fields from the document text provided.\n"
    "Return ONLY a valid JSON object with exactly the keys listed below.\n"
    "If a text/numeric field is not present or not clearly stated in the text, set its value to null.\n"
    "For presence/signed flags, set to boolean true or false (default false if not present).\n"
    "Do NOT guess, infer, or hallucinate values that are not explicitly present in the text.\n"
    "Do NOT add extra keys beyond those listed.\n\n"
    "Extract these fields:\n"
    "- applicant_name          : Full name of the applicant / borrower / customer\n"
    "- fathers_name            : Father's full name\n"
    "- dob                     : Date of birth (preserve original format exactly)\n"
    "- mobile_no               : Mobile or phone number\n"
    "- gender                  : Gender (Male / Female / Other)\n"
    "- aadhaar_number          : 12-digit Aadhaar UID or masked UID (e.g. XXXXXXXX5552)\n"
    "- pan_number              : PAN number (format: AAAAA9999A — five letters, four digits, one letter)\n"
    "- address                 : Full address text as it appears in the document\n"
    "- current_address         : Current / residential address if separately stated\n"
    "- bank_account_no         : Bank account number\n"
    "- type_of_account         : Type of bank account (SB / CA / CC / etc.)\n"
    "- loan_amount             : Loan / sanctioned / disbursed amount — digits only, no currency symbol\n"
    "- loan_validity           : Loan tenure or period (e.g. '36 Months', '2 years')\n"
    "- loan_type               : Type of loan (e.g. 'TW', 'Personal Loan', 'Home Loan')\n"
    "- application_no          : Application number / application ID\n"
    "- application_date        : Date of application (preserve original format)\n"
    "- BPI                     : Broken Period Interest (BPI) amount if stated (digits/float or null)\n"
    "- irr_percent             : Contractual Interest Rate (ROI) or Internal Rate of Return (IRR) % (e.g. 17.0). Must be the base/nominal rate (labeled 'Interest Rate', 'Rate of Interest', or 'ROI'). NEVER extract APR (Annual Percentage Rate) into this field. If both Interest Rate and APR are present, always extract the Interest Rate / IRR.\n"
    "- emi                     : Equated Monthly Installment (EMI / EPI) amount\n"
    "- customer_consent        : Is explicit customer consent, OTP verification (e.g. 'Customer consent provided on KFS via OTP...'), or borrower acceptance present? (boolean: true / false)\n"
)


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
    this_mod = sys.modules[__name__]
    effective_api_key = getattr(this_mod, "LLM_API_KEY", LLM_API_KEY)
    effective_model = getattr(this_mod, "LLM_MODEL", LLM_MODEL)
    effective_base_url = getattr(this_mod, "LLM_BASE_URL", LLM_BASE_URL)
    effective_temperature = getattr(this_mod, "LLM_TEMPERATURE", LLM_TEMPERATURE)

    if not effective_api_key:
        logger.warning("[%s] LLM_API_KEY not set — skipping LLM field extraction", doc_id)
        return {}

    if not raw_text or not raw_text.strip():
        logger.warning("[%s] Empty OCR text — skipping LLM field extraction", doc_id)
        return {}

    user_content = _build_user_content(doc_type, raw_text)

    try:
        extracted = invoke_llm_json(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_content,
            model=effective_model,
            api_key=effective_api_key,
            base_url=effective_base_url,
            temperature=effective_temperature,
            max_tokens=LLM_MAX_TOKENS,
            timeout=60.0,
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
            effective_model,
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
