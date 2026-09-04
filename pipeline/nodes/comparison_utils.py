import datetime
import logging
import re
from typing import Any

import jellyfish
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from pipeline.config import (
    ADDRESS_MATCH_ALGO,
    BROKEN_PERIOD_INTEREST_TOLERANCE_PCT,
    FUZZY_MATCH_BAND,
    FUZZY_PARTIAL_LOWER,
    LOAN_AMOUNT_THRESHOLD_PCT,
    NAME_MATCH_ALGO,
)
from pipeline.nodes.llm_adjudicator import llm_adjudicate

logger = logging.getLogger("disbursement_pipeline.comparison_utils")

# Document aliases mapping for robust extraction key resolution
DOC_ALIASES: dict[str, list[str]] = {
    "aadhaar": ["aadhaar", "kyc_address_proof", "aadhaar_card", "aadhar"],
    "pan": ["pan", "kyc_pan", "pan_card"],
    "application_form": ["application_form", "loan_application"],
    "account_statement": ["account_statement", "bank_statement", "bank_account_statement"],
    "kfs": ["kfs", "key_fact_statement"],
    "disbursal_memo": ["disbursal_memo", "disbursement_memo", "memo"],
    "sanction_letter": ["sanction_letter", "sanction"],
}


def resolve_doc_data(extracted_data: dict[str, Any], doc_type: str) -> dict[str, Any] | None:
    """Finds extracted document data by checking primary key and known aliases."""
    if not extracted_data:
        return None
    candidate_keys = DOC_ALIASES.get(doc_type, [doc_type])
    for key in candidate_keys:
        if key in extracted_data and isinstance(extracted_data[key], dict):
            return extracted_data[key]
    return None


def clean_numeric(val: Any) -> float | None:
    """Extracts float from numeric/string representations, handling currency symbols and commas."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    val_str = str(val).strip()
    if not val_str:
        return None
    cleaned = re.sub(r"[^\d.]", "", val_str)
    try:
        return float(cleaned)
    except ValueError:
        return None


def clean_id(val: Any) -> str | None:
    """Normalizes identification numbers by removing whitespace and uppercasing."""
    if val is None:
        return None
    cleaned = re.sub(r"\s+", "", str(val)).upper()
    return cleaned if cleaned else None


def clean_string(val: Any) -> str:
    """Trims whitespace and returns clean string."""
    if val is None:
        return ""
    return str(val).strip()


def normalize_date(val: Any) -> str | None:
    """Normalizes date strings to YYYY-MM-DD if recognizable, else returns trimmed string."""
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None

    # Try common date formats
    date_formats = [
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%Y/%m/%d",
        "%d.%m.%Y",
        "%d %b %Y",
        "%d %B %Y",
    ]
    for fmt in date_formats:
        try:
            dt = datetime.datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

    # Fallback to normalized lowercase trimmed string
    return s.lower()


def compute_tfidf_cosine(text1: str, text2: str) -> float:
    """Computes TF-IDF cosine similarity between two text snippets."""
    t1 = clean_string(text1)
    t2 = clean_string(text2)
    if not t1 or not t2:
        return 0.0
    if t1.lower() == t2.lower():
        return 1.0
    try:
        vectorizer = TfidfVectorizer().fit([t1, t2])
        vectors = vectorizer.transform([t1, t2])
        sim = cosine_similarity(vectors[0:1], vectors[1:2])[0][0]
        return float(sim)
    except (ValueError, TypeError) as e:
        logger.warning("TF-IDF cosine computation failed: %s", e)
        return 0.0


def extract_field_value(doc_dict: dict[str, Any], field_name: str, aliases: list[str] | None = None) -> Any:
    """Retrieves value from document dictionary checking canonical field name and aliases."""
    if field_name in doc_dict and doc_dict[field_name] is not None:
        return doc_dict[field_name]
    if aliases:
        for alias in aliases:
            if alias in doc_dict and doc_dict[alias] is not None:
                return doc_dict[alias]
    return None


def run_field_checks(
    doc_type: str,
    doc_data: dict[str, Any] | None,
    los_data: dict[str, Any],
    field_checks: list[dict[str, Any]],
    loan_id: str,
    subnode_name: str,
) -> list[dict[str, Any]]:
    """Runs configured field comparisons between a document and LOS.
    
    If doc_data is None or empty, emits NOT_FOUND records for all expected fields.
    If a field is missing in doc_data or los_data, emits NOT_FOUND with confidence 0.0.
    Triggers LLM adjudication for PARTIAL fuzzy matches.
    """
    records: list[dict[str, Any]] = []

    for check in field_checks:
        doc_field = check["doc_field"]
        los_field = check.get("los_field")
        method = check.get("method", "exact_string")
        aliases = check.get("aliases", [])
        is_optional = check.get("optional", False)

        check_id = f"chk_{subnode_name}_{doc_type}_{doc_field}_vs_los"

        # Case 1: Entire document missing
        if doc_data is None:
            if is_optional:
                continue
            records.append({
                "check_id": check_id,
                "subnode": subnode_name,
                "field": doc_field,
                "sources": [doc_type, "los"],
                "values": [None, los_data.get(los_field) if los_field else None],
                "match_type": _get_match_type_label(method),
                "match_status": "NOT_FOUND",
                "confidence": 0.0,
                "method": method,
                "llm_used": False,
                "notes": f"Document '{doc_type}' not found in extracted data",
            })
            continue

        raw_doc_val = extract_field_value(doc_data, doc_field, aliases)
        raw_los_val = los_data.get(los_field) if los_field else None

        # Binary presence-only check (e.g. customer_consent)
        if method == "presence_only":
            records.append(_evaluate_presence(check_id, subnode_name, doc_type, doc_field, raw_doc_val))
            continue

        # Missing field in either doc or LOS
        if raw_doc_val is None or (los_field and raw_los_val is None):
            if is_optional and raw_doc_val is None:
                continue
            missing_source = doc_type if raw_doc_val is None else "los"
            records.append({
                "check_id": check_id,
                "subnode": subnode_name,
                "field": doc_field,
                "sources": [doc_type, "los"],
                "values": [raw_doc_val, raw_los_val],
                "match_type": _get_match_type_label(method),
                "match_status": "NOT_FOUND",
                "confidence": 0.0,
                "method": method,
                "llm_used": False,
                "notes": f"Missing value in {missing_source}",
            })
            continue

        # Execute specific comparison method
        record = _evaluate_comparison(
            check_id=check_id,
            subnode_name=subnode_name,
            doc_type=doc_type,
            doc_field=doc_field,
            method=method,
            raw_doc_val=raw_doc_val,
            raw_los_val=raw_los_val,
        )

        # Trigger LLM adjudication if fuzzy match returned PARTIAL
        if record["match_type"] == "fuzzy" and record["match_status"] == "PARTIAL":
            val_a, val_b = record["values"][0], record["values"][1]
            adjudication = llm_adjudicate(str(val_a), str(val_b), doc_field, loan_id)
            record["match_status"] = adjudication["match_status"]
            record["llm_used"] = adjudication["llm_used"]
            if adjudication.get("confidence") is not None:
                record["confidence"] = adjudication["confidence"]
            note_parts = [record.get("notes"), f"Adjudication: {adjudication.get('reason')}"]
            record["notes"] = " | ".join(p for p in note_parts if p)

        records.append(record)

    return records


def _get_match_type_label(method: str) -> str:
    """Returns canonical match_type category for state ResultRecord."""
    if method in ("jaro_winkler", "tfidf_cosine"):
        return "fuzzy"
    if method in ("exact_id",):
        return "exact_id"
    if method in ("exact_date",):
        return "exact_date"
    if method in ("threshold_90", "tolerance_numeric"):
        return "threshold"
    if method in ("presence_only",):
        return "presence"
    return "exact_string"


def _evaluate_presence(
    check_id: str, subnode_name: str, doc_type: str, doc_field: str, raw_val: Any
) -> dict[str, Any]:
    """Evaluates presence-only check (e.g. customer consent)."""
    if raw_val is None:
        is_present = False
    elif isinstance(raw_val, bool):
        is_present = raw_val
    elif isinstance(raw_val, (int, float)):
        is_present = raw_val > 0
    else:
        s = str(raw_val).strip().lower()
        is_present = s in ("true", "1", "yes", "y", "verified", "accepted", "consented")

    return {
        "check_id": check_id,
        "subnode": subnode_name,
        "field": doc_field,
        "sources": [doc_type],
        "values": [raw_val],
        "match_type": "presence",
        "match_status": "MATCH" if is_present else "MISMATCH",
        "confidence": 1.0 if is_present else 0.0,
        "method": "presence_only",
        "llm_used": False,
        "notes": None if is_present else f"Field '{doc_field}' missing or not accepted in {doc_type}",
    }


def _evaluate_comparison(
    check_id: str,
    subnode_name: str,
    doc_type: str,
    doc_field: str,
    method: str,
    raw_doc_val: Any,
    raw_los_val: Any,
) -> dict[str, Any]:
    """Dispatches comparison based on method and generates ResultRecord."""
    match_type = _get_match_type_label(method)

    if method == "jaro_winkler":
        s_doc = clean_string(raw_doc_val)
        s_los = clean_string(raw_los_val)
        if not s_doc or not s_los:
            score = 0.0
            status = "MISMATCH"
        else:
            score = jellyfish.jaro_winkler_similarity(s_doc.lower(), s_los.lower())
            if score >= FUZZY_MATCH_BAND:
                status = "MATCH"
            elif score >= FUZZY_PARTIAL_LOWER:
                status = "PARTIAL"
            else:
                status = "MISMATCH"
        return {
            "check_id": check_id,
            "subnode": subnode_name,
            "field": doc_field,
            "sources": [doc_type, "los"],
            "values": [raw_doc_val, raw_los_val],
            "match_type": match_type,
            "match_status": status,
            "confidence": round(score, 4),
            "method": NAME_MATCH_ALGO,
            "llm_used": False,
            "notes": None if status == "MATCH" else f"Similarity: {score:.4f}",
        }

    if method == "tfidf_cosine":
        s_doc = clean_string(raw_doc_val)
        s_los = clean_string(raw_los_val)
        sim = compute_tfidf_cosine(s_doc, s_los)
        if sim >= FUZZY_MATCH_BAND:
            status = "MATCH"
        elif sim >= FUZZY_PARTIAL_LOWER:
            status = "PARTIAL"
        else:
            status = "MISMATCH"
        return {
            "check_id": check_id,
            "subnode": subnode_name,
            "field": doc_field,
            "sources": [doc_type, "los"],
            "values": [raw_doc_val, raw_los_val],
            "match_type": match_type,
            "match_status": status,
            "confidence": round(sim, 4),
            "method": ADDRESS_MATCH_ALGO,
            "llm_used": False,
            "notes": None if status == "MATCH" else f"TF-IDF cosine similarity: {sim:.4f}",
        }

    if method == "exact_id":
        id_doc = clean_id(raw_doc_val)
        id_los = clean_id(raw_los_val)
        is_match = bool(id_doc and id_los and id_doc == id_los)
        return {
            "check_id": check_id,
            "subnode": subnode_name,
            "field": doc_field,
            "sources": [doc_type, "los"],
            "values": [id_doc, id_los],
            "match_type": match_type,
            "match_status": "MATCH" if is_match else "MISMATCH",
            "confidence": 1.0 if is_match else 0.0,
            "method": "exact_id_equality",
            "llm_used": False,
            "notes": None if is_match else f"ID mismatch: {id_doc} vs {id_los}",
        }

    if method == "exact_date":
        d_doc = normalize_date(raw_doc_val)
        d_los = normalize_date(raw_los_val)
        is_match = bool(d_doc and d_los and d_doc == d_los)
        return {
            "check_id": check_id,
            "subnode": subnode_name,
            "field": doc_field,
            "sources": [doc_type, "los"],
            "values": [raw_doc_val, raw_los_val],
            "match_type": match_type,
            "match_status": "MATCH" if is_match else "MISMATCH",
            "confidence": 1.0 if is_match else 0.0,
            "method": "normalized_date_equality",
            "llm_used": False,
            "notes": None if is_match else f"Date mismatch: {d_doc} vs {d_los}",
        }

    if method == "exact_string_ci":
        s_doc = clean_string(raw_doc_val).lower()
        s_los = clean_string(raw_los_val).lower()
        is_match = bool(s_doc and s_los and s_doc == s_los)
        return {
            "check_id": check_id,
            "subnode": subnode_name,
            "field": doc_field,
            "sources": [doc_type, "los"],
            "values": [raw_doc_val, raw_los_val],
            "match_type": "exact_string",
            "match_status": "MATCH" if is_match else "MISMATCH",
            "confidence": 1.0 if is_match else 0.0,
            "method": "case_insensitive_string_equality",
            "llm_used": False,
            "notes": None if is_match else f"String mismatch: '{raw_doc_val}' vs '{raw_los_val}'",
        }

    if method == "threshold_90":
        n_doc = clean_numeric(raw_doc_val)
        n_los = clean_numeric(raw_los_val)
        if n_doc is None or n_los is None:
            return {
                "check_id": check_id,
                "subnode": subnode_name,
                "field": doc_field,
                "sources": [doc_type, "los"],
                "values": [n_doc, n_los],
                "match_type": match_type,
                "match_status": "NOT_FOUND",
                "confidence": 0.0,
                "method": "threshold_90_compare",
                "llm_used": False,
                "notes": f"Could not parse numeric value: {raw_doc_val} vs {raw_los_val}",
            }
        if n_doc <= 0 or n_los <= 0:
            is_match = (n_doc == n_los)
            ratio = 1.0 if is_match else 0.0
        else:
            ratio = min(n_doc, n_los) / max(n_doc, n_los)
            is_match = ratio >= LOAN_AMOUNT_THRESHOLD_PCT

        return {
            "check_id": check_id,
            "subnode": subnode_name,
            "field": doc_field,
            "sources": [doc_type, "los"],
            "values": [n_doc, n_los],
            "match_type": match_type,
            "match_status": "MATCH" if is_match else "MISMATCH",
            "confidence": round(min(1.0, ratio), 4),
            "method": "threshold_90_compare",
            "llm_used": False,
            "notes": None if is_match else f"Amount ratio ({ratio:.4f}) below {int(LOAN_AMOUNT_THRESHOLD_PCT * 100)}% threshold ({n_doc} vs {n_los})",
        }

    # Default fallback: exact string equality
    s_doc = clean_string(raw_doc_val)
    s_los = clean_string(raw_los_val)
    is_match = bool(s_doc and s_los and s_doc == s_los)
    return {
        "check_id": check_id,
        "subnode": subnode_name,
        "field": doc_field,
        "sources": [doc_type, "los"],
        "values": [raw_doc_val, raw_los_val],
        "match_type": "exact_string",
        "match_status": "MATCH" if is_match else "MISMATCH",
        "confidence": 1.0 if is_match else 0.0,
        "method": "exact_string_equality",
        "llm_used": False,
        "notes": None if is_match else f"Mismatch: '{raw_doc_val}' vs '{raw_los_val}'",
    }


def compare_bpi_doc_to_doc(
    kfs_data: dict[str, Any] | None,
    memo_data: dict[str, Any] | None,
    subnode_name: str = "kfs_sanction",
) -> dict[str, Any] | None:
    """Doc-to-doc consistency check between KFS and Disbursal Memo for Broken Period Interest (BPI).
    
    Optional: If neither document contains BPI, returns None (no check emitted).
    If only one has BPI and other has None, emits NOT_FOUND.
    If both have BPI, verifies consistency within BROKEN_PERIOD_INTEREST_TOLERANCE_PCT.
    """
    kfs_bpi_raw = None
    if kfs_data:
        kfs_bpi_raw = extract_field_value(kfs_data, "bpi_charge", ["broken_period_interest", "bpi"])

    memo_bpi_raw = None
    if memo_data:
        memo_bpi_raw = extract_field_value(memo_data, "bpi_charge", ["broken_period_interest", "bpi"])

    kfs_bpi = clean_numeric(kfs_bpi_raw)
    memo_bpi = clean_numeric(memo_bpi_raw)

    # If neither document provides BPI, skip gracefully
    if kfs_bpi is None and memo_bpi is None:
        return None

    check_id = f"chk_{subnode_name}_kfs_vs_disbursal_memo_bpi_charge"

    if kfs_bpi is None or memo_bpi is None:
        missing_doc = "KFS" if kfs_bpi is None else "Disbursal Memo"
        return {
            "check_id": check_id,
            "subnode": subnode_name,
            "field": "bpi_charge",
            "sources": ["kfs", "disbursal_memo"],
            "values": [kfs_bpi, memo_bpi],
            "match_type": "threshold",
            "match_status": "NOT_FOUND",
            "confidence": 0.0,
            "method": "doc_to_doc_tolerance",
            "llm_used": False,
            "notes": f"Broken period interest missing in {missing_doc}",
        }

    diff = abs(kfs_bpi - memo_bpi)
    denom = max(kfs_bpi, memo_bpi)
    if denom == 0.0:
        is_match = True
        ratio = 1.0
    else:
        is_match = (diff / denom) <= BROKEN_PERIOD_INTEREST_TOLERANCE_PCT
        ratio = 1.0 - (diff / denom)

    return {
        "check_id": check_id,
        "subnode": subnode_name,
        "field": "bpi_charge",
        "sources": ["kfs", "disbursal_memo"],
        "values": [kfs_bpi, memo_bpi],
        "match_type": "threshold",
        "match_status": "MATCH" if is_match else "MISMATCH",
        "confidence": round(max(0.0, min(1.0, ratio)), 4),
        "method": "doc_to_doc_tolerance",
        "llm_used": False,
        "notes": (
            f"BPI matched within {int(BROKEN_PERIOD_INTEREST_TOLERANCE_PCT * 100)}% tolerance ({kfs_bpi} vs {memo_bpi})"
            if is_match
            else f"BPI mismatch: KFS ({kfs_bpi}) vs Disbursal Memo ({memo_bpi}) exceeds tolerance"
        ),
    }
