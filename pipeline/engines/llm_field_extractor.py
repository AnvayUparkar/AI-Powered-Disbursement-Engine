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
    "aadhaar_xml_present",
    "loan_agreement_present",
    "loan_agreement_signed",
    "customer_consent",
)

_CANONICAL_KEYS: frozenset[str] = frozenset(TEMPLATE_FIELDS)


def format_template_json(extracted: dict[str, Any] | None) -> dict[str, Any]:
    """Formats an arbitrary extracted dictionary into the exact 23-field canonical template.

    Keys are returned in the exact canonical order with non-present fields as None,
    and boolean flags (aadhaar_xml_present, loan_agreement_present, loan_agreement_signed, customer_consent)
    as False by default.
    """
    boolean_keys = {"aadhaar_xml_present", "loan_agreement_present", "loan_agreement_signed", "customer_consent"}
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
    return result

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
    "- aadhaar_xml_present     : Is an Aadhaar XML or e-Aadhaar QR/XML verification block present? (boolean: true / false)\n"
    "- loan_agreement_present  : Is a loan agreement present? (boolean: true / false)\n"
    "- loan_agreement_signed   : Is the loan agreement signed or e-signed? (boolean: true / false)\n"
    "- customer_consent        : Is explicit customer consent, OTP verification (e.g. 'Customer consent provided on KFS via OTP...'), or borrower acceptance present? (boolean: true / false)\n"
)

_OPENROUTER_URL: str = "https://openrouter.ai/api/v1/chat/completions"

_OPENROUTER_HEADERS: dict[str, str] = {
    "Content-Type": "application/json",
    "HTTP-Referer": "https://disbursement-scorecard",
    "X-Title": "Disbursement Scorecard - OCR Field Extraction",
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


def _extract_with_gemini(
    user_content: str,
    api_key: str,
    model: str,
    doc_id: str,
    doc_type: str,
) -> dict[str, Any]:
    """Extracts structured fields using Google Gemini direct API."""
    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_google_genai import ChatGoogleGenerativeAI

        model_name = model
        if model_name in ("gemini-2.5-flash-lite", "google/gemini-2.5-flash-lite"):
            model_name = "gemini-3.5-flash-lite"
        elif "/" in model_name and "gemini" in model_name.lower():
            model_name = model_name.split("/")[-1]

        client = ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=api_key,
            temperature=0.0,
            max_retries=2,
            timeout=45.0,
        )
        messages = [
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=user_content),
        ]
        ai_msg = client.invoke(messages)

        raw_content = ""
        if isinstance(ai_msg.content, str):
            raw_content = ai_msg.content
        elif isinstance(ai_msg.content, list):
            texts = []
            for item in ai_msg.content:
                if isinstance(item, dict) and "text" in item:
                    texts.append(str(item["text"]))
                elif isinstance(item, str):
                    texts.append(item)
                elif hasattr(item, "text"):
                    texts.append(str(item.text))
            raw_content = "\n".join(texts)
        else:
            raw_content = str(ai_msg.content)

        cleaned = _clean_json_response(raw_content)
        extracted: dict[str, Any] = json.loads(cleaned)
        result: dict[str, Any] = format_template_json(extracted)

        non_null = sum(1 for v in result.values() if v is not None and v is not False)
        logger.info(
            "[%s] Gemini direct extracted %d/%d non-null fields (doc_type=%s, model=%s)",
            doc_id,
            non_null,
            len(TEMPLATE_FIELDS),
            doc_type,
            model_name,
        )
        return result
    except Exception as e:
        logger.error("[%s] Direct Gemini extraction failed: %s", doc_id, e)
        return {}


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
    effective_api_key = LLM_API_KEY
    effective_model = LLM_MODEL

    if not effective_api_key:
        logger.warning("[%s] LLM_API_KEY not set — skipping LLM field extraction", doc_id)
        return {}

    if not raw_text or not raw_text.strip():
        logger.warning("[%s] Empty OCR text — skipping LLM field extraction", doc_id)
        return {}

    user_content = _build_user_content(doc_type, raw_text)

    is_gemini = (
        effective_api_key.startswith("AQ.")
        or effective_api_key.startswith("AIza")
        or "gemini" in str(effective_model).lower()
    )
    if is_gemini:
        return _extract_with_gemini(user_content, effective_api_key, effective_model, doc_id, doc_type)

    payload: dict[str, Any] = {
        "model": effective_model,
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
                    "Authorization": f"Bearer {effective_api_key}",
                },
                json=payload,
            )
            response.raise_for_status()

        data: dict[str, Any] = response.json()
        raw_content: str = data["choices"][0]["message"]["content"]
        cleaned = _clean_json_response(raw_content)
        extracted: dict[str, Any] = json.loads(cleaned)
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
