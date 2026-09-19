"""LLM Adjudicator — Adjudicates borderline/fuzzy match results using configured LLM provider."""
import json
import logging
import os
import re
from typing import Any, Dict, Optional

from config.settings import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_TEMPERATURE,
)
from pipeline.audit import append_audit_entry
from pipeline.engines.llm_client import invoke_llm_json

logger = logging.getLogger("disbursement_pipeline.llm_adjudicator")


def llm_adjudicate(value_a: Any, value_b: Any, field_type: str, loan_id: str) -> dict:
    """Calls configured LLM provider to adjudicate PARTIAL-band fuzzy matches.

    (names via Jaro-Winkler, addresses via TF-IDF cosine).
    Falls back gracefully to PARTIAL/manual review if API key is not configured or on failure.
    """
    str_a = str(value_a) if value_a is not None else ""
    str_b = str(value_b) if value_b is not None else ""

    api_key = os.getenv("LLM_API_KEY") or LLM_API_KEY
    model = os.getenv("LLM_MODEL") or LLM_MODEL
    base_url = os.getenv("LLM_BASE_URL") or LLM_BASE_URL
    temperature = float(os.getenv("LLM_TEMPERATURE", str(LLM_TEMPERATURE)))

    if not api_key:
        logger.warning(
            "[FALLBACK] LLM API key not configured — fallback for %s: '%s' vs '%s'",
            field_type,
            str_a,
            str_b,
        )
        fallback_res = {
            "match_status": "PARTIAL",
            "confidence": 0.5,
            "reason": "LLM API key not configured. Flagged for manual review.",
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
        parsed = invoke_llm_json(
            system_prompt=system_prompt,
            user_prompt=human_prompt,
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
            max_tokens=1024,
            timeout=30.0,
        )

        status = parsed.get("match_status", "PARTIAL").upper()
        if status not in ("MATCH", "MISMATCH", "PARTIAL"):
            status = "PARTIAL"

        confidence = float(parsed.get("confidence", 0.9))
        reason = parsed.get("reason", "LLM adjudication completed.")

        result = {
            "match_status": status,
            "confidence": round(confidence, 4),
            "reason": reason,
            "llm_used": True,
        }

        logger.info(
            "LLM adjudicated %s for loan %s: %s (confidence=%.2f, model=%s)",
            field_type,
            loan_id,
            status,
            confidence,
            model,
        )

        append_audit_entry(
            loan_id,
            {
                "type": "llm_adjudication",
                "model": model,
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
        logger.error("LLM adjudication failed for %s (loan %s): %s", field_type, loan_id, e)
        fallback_res = {
            "match_status": "PARTIAL",
            "confidence": 0.5,
            "reason": f"LLM adjudication encountered a service error ({e}). Flagged for manual review.",
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
