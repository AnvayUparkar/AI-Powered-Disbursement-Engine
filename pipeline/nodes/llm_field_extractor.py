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

from config import LLM_API_KEY, LLM_MODEL

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

# Canonical field names accepted in the LLM response.
# Any extra keys returned by the LLM are discarded before returning.
_CANONICAL_KEYS: frozenset[str] = frozenset({
    "applicant_name",
    "fathers_name",
    "dob",
    "mobile_no",
    "gender",
    "aadhaar_number",
    "pan_number",
    "address",
    "current_address",
    "account_no",
    "type_of_account",
    "loan_amount",
    "loan_validity",
    "loan_type",
    "loan_account_no",
    "loan_no",
    "application_no",
    "application_date",
    "login_date",
    "disbursement_date",
    "customer_consent",
})

# ── Universal extraction prompt ────────────────────────────────────────────
_SYSTEM_PROMPT: str = (
    "You are a financial document field extraction engine.\n"
    "Your task: extract specific fields from the document text provided.\n"
    "Return ONLY a valid JSON object with exactly the keys listed below.\n"
    "If a field is not present or not clearly stated in the text, set its value to null.\n"
    "Do NOT guess, infer, or hallucinate values that are not explicitly present in the text.\n"
    "Do NOT add extra keys beyond those listed.\n\n"
    "Extract these fields:\n"
    "- applicant_name    : Full name of the applicant / borrower / customer\n"
    "- fathers_name      : Father's full name\n"
    "- dob               : Date of birth (preserve original format exactly)\n"
    "- mobile_no         : Mobile or phone number\n"
    "- gender            : Gender (Male / Female / Other)\n"
    "- aadhaar_number    : 12-digit Aadhaar UID (preserve spaces if present)\n"
    "- pan_number        : PAN number (format: AAAAA9999A — five letters, four digits, one letter)\n"
    "- address           : Full address text as it appears in the document\n"
    "- current_address   : Current / residential address if separately stated\n"
    "- account_no        : Bank account number\n"
    "- type_of_account   : Type of bank account (SB / CA / CC / etc.)\n"
    "- loan_amount       : Loan / sanctioned / disbursed amount — digits only, no currency symbol\n"
    "- loan_validity     : Loan tenure or period (e.g. '24 months', '2 years')\n"
    "- loan_type         : Type of loan (e.g. 'Personal Loan', 'Home Loan')\n"
    "- loan_account_no   : Loan account number\n"
    "- loan_no           : Loan number / application number\n"
    "- application_no    : Application number / application ID\n"
    "- application_date  : Date of application (preserve original format)\n"
    "- login_date        : Login date (preserve original format)\n"
    "- disbursement_date : Disbursement date (preserve original format)\n"
    "- customer_consent  : Is customer consent / signature explicitly present? (true / false / null)\n"
)

_OPENROUTER_URL: str = "https://openrouter.ai/api/v1/chat/completions"

_OPENROUTER_HEADERS: dict[str, str] = {
    "Content-Type": "application/json",
    "HTTP-Referer": "https://disbursement-scorecard",
    "X-Title": "Disbursement Scorecard — OCR Field Extraction",
}


# ── Helpers ────────────────────────────────────────────────────────────────

def _clean_json_response(text: str) -> str:
    """Strips markdown code fences if the LLM wraps its JSON output."""
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return match.group(1)
    match = re.search(r"(\{.*\})", text, re.DOTALL)
    if match:
        return match.group(1)
    return text.strip()


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
    """Sends raw OCR text to OpenRouter and returns a structured field dict.

    Args:
        doc_type: Canonical document type key (e.g. ``"aadhaar"``, ``"kfs"``).
        raw_text: Raw OCR text from ``ParsedDocument.text``.
        doc_id:   Document ID used for structured logging.

    Returns:
        Dict mapping canonical field names -> extracted values (``None`` for
        fields not found in the document).  Returns ``{}`` on any failure so
        the caller can proceed gracefully without a crash.
    """
    if not LLM_API_KEY:
        logger.warning("[%s] LLM_API_KEY not set — skipping LLM field extraction", doc_id)
        return {}

    if not raw_text or not raw_text.strip():
        logger.warning("[%s] Empty OCR text — skipping LLM field extraction", doc_id)
        return {}

    user_content = _build_user_content(doc_type, raw_text)

    payload: dict[str, Any] = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }

    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                _OPENROUTER_URL,
                headers={
                    **_OPENROUTER_HEADERS,
                    "Authorization": f"Bearer {LLM_API_KEY}",
                },
                json=payload,
            )
            response.raise_for_status()

        data: dict[str, Any] = response.json()
        raw_content: str = data["choices"][0]["message"]["content"]
        cleaned = _clean_json_response(raw_content)
        extracted: dict[str, Any] = json.loads(cleaned)

        # Discard extra keys the LLM may have added despite instructions.
        result: dict[str, Any] = {k: v for k, v in extracted.items() if k in _CANONICAL_KEYS}

        non_null = sum(1 for v in result.values() if v is not None)
        logger.info(
            "[%s] LLM extracted %d/%d non-null fields (doc_type=%s, model=%s)",
            doc_id,
            non_null,
            len(_CANONICAL_KEYS),
            doc_type,
            LLM_MODEL,
        )
        return result

    except httpx.HTTPStatusError as e:
        logger.error(
            "[%s] OpenRouter HTTP %s: %s",
            doc_id,
            e.response.status_code,
            e.response.text[:500],
        )
    except httpx.TimeoutException:
        logger.error("[%s] OpenRouter request timed out (doc_type=%s)", doc_id, doc_type)
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.error("[%s] Failed to parse LLM JSON response: %s", doc_id, e)
    except Exception as e:  # noqa: BLE001 — defensive boundary, always return {}
        logger.error("[%s] Unexpected error in LLM field extraction: %s", doc_id, e)

    return {}
