import json
import logging
import re
from functools import lru_cache
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from config import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_TEMPERATURE
from pipeline.audit import append_audit_entry

logger = logging.getLogger("disbursement_pipeline.llm_adjudicator")


@lru_cache(maxsize=1)
def _get_gemini_client() -> ChatGoogleGenerativeAI | None:
    """Returns a cached singleton instance of the Gemini chat client."""
    if not GEMINI_API_KEY:
        return None
    try:
        return ChatGoogleGenerativeAI(
            model=GEMINI_MODEL,
            google_api_key=GEMINI_API_KEY,
            temperature=GEMINI_TEMPERATURE,
            max_retries=3,
            timeout=30.0,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to initialize ChatGoogleGenerativeAI: %s", e)
        return None


def _extract_text(content: Any) -> str:
    """Safely extracts plain text from LangChain message content."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict) and "text" in item:
                texts.append(str(item["text"]))
            elif isinstance(item, str):
                texts.append(item)
            elif hasattr(item, "text"):
                texts.append(str(item.text))
        return "\n".join(texts)
    return str(content)


def _clean_json_text(text: str) -> str:
    """Extracts JSON substring if wrapped in markdown code blocks or text."""
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return match.group(1)
    match_braces = re.search(r"(\{.*\})", text, re.DOTALL)
    if match_braces:
        return match_braces.group(1)
    return text.strip()


def llm_adjudicate(value_a: Any, value_b: Any, field_type: str, loan_id: str) -> dict:
    """Calls Google Gemini to adjudicate PARTIAL-band fuzzy matches

    (names via Jaro-Winkler, addresses via TF-IDF cosine).
    Falls back gracefully to PARTIAL/manual review if API key is not configured or on failure.
    """
    str_a = str(value_a) if value_a is not None else ""
    str_b = str(value_b) if value_b is not None else ""

    client = _get_gemini_client()

    if not client:
        logger.warning(
            "[FALLBACK] Gemini API key not configured — fallback for %s: '%s' vs '%s'",
            field_type,
            str_a,
            str_b,
        )
        fallback_res = {
            "match_status": "PARTIAL",
            "confidence": 0.5,
            "reason": "Gemini API key not configured. Flagged for manual review.",
            "llm_used": False,
        }
        append_audit_entry(
            loan_id,
            {
                "type": "llm_adjudication",
                "field_type": field_type,
                "value_a": str_a,
                "value_b": str_b,
                "adjudication_status": fallback_res["match_status"],
                "reason": fallback_res["reason"],
                "llm_used": False,
            },
        )
        return fallback_res

    system_prompt = (
        "You are an expert loan document verification and entity resolution auditor.\n"
        "Your task is to compare two values extracted from loan documents (e.g. KYC document vs Application form) "
        "that yielded borderline similarity scores.\n\n"
        "Determine if Value A and Value B refer to the exact same entity, person, or address.\n"
        "- Account for common OCR noise, name abbreviation (e.g., 'Mohd' vs 'Mohammad', initial expansions), "
        "honorifics, address reordering, or standard abbreviations (e.g., 'Rd' vs 'Road', 'Apt' vs 'Apartment').\n"
        "- If they refer to the SAME entity/person/address, return match_status: 'MATCH'.\n"
        "- If they clearly refer to DIFFERENT entities/persons/places, return match_status: 'MISMATCH'.\n"
        "- If ambiguous or insufficient info, return match_status: 'PARTIAL'.\n\n"
        "You must respond ONLY with a JSON object in this exact schema:\n"
        "{\n"
        '  "match_status": "MATCH" | "MISMATCH" | "PARTIAL",\n'
        '  "confidence": <float between 0.0 and 1.0>,\n'
        '  "reason": "<1-2 sentence concise justification>"\n'
        "}"
    )

    human_prompt = (
        f"Field Type: {field_type}\n"
        f"Value A (Document 1): {str_a}\n"
        f"Value B (Document 2): {str_b}\n"
        f"Loan ID: {loan_id}"
    )

    try:
        response = client.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_prompt),
        ])

        raw_content = _extract_text(response.content)
        cleaned_json = _clean_json_text(raw_content)
        parsed = json.loads(cleaned_json)

        status = parsed.get("match_status", "PARTIAL").upper()
        if status not in ("MATCH", "MISMATCH", "PARTIAL"):
            status = "PARTIAL"

        confidence = float(parsed.get("confidence", 0.9))
        reason = parsed.get("reason", "Gemini adjudication completed.")

        result = {
            "match_status": status,
            "confidence": round(confidence, 4),
            "reason": reason,
            "llm_used": True,
        }

        logger.info(
            "Gemini adjudicated %s for loan %s: %s (confidence=%.2f)",
            field_type,
            loan_id,
            status,
            confidence,
        )

        append_audit_entry(
            loan_id,
            {
                "type": "llm_adjudication",
                "model": GEMINI_MODEL,
                "field_type": field_type,
                "value_a": str_a,
                "value_b": str_b,
                "adjudication_status": status,
                "confidence": confidence,
                "reason": reason,
                "llm_used": True,
            },
        )
        return result

    except Exception as e:  # noqa: BLE001 - Fallback on any unexpected LLM failure
        logger.error("Gemini adjudication failed for %s (loan %s): %s", field_type, loan_id, e)
        fallback_res = {
            "match_status": "PARTIAL",
            "confidence": 0.5,
            "reason": "Gemini adjudication encountered a service error. Flagged for manual review.",
            "llm_used": False,
        }
        append_audit_entry(
            loan_id,
            {
                "type": "llm_adjudication_error",
                "field_type": field_type,
                "value_a": str_a,
                "value_b": str_b,
                "error": str(e),
                "adjudication_status": "PARTIAL",
                "llm_used": False,
            },
        )
        return fallback_res

