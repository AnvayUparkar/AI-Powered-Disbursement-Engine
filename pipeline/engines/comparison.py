"""Comparison engine — matching algorithms, data normalization, and field verification runners."""
import datetime
import logging
import re
from typing import Any, Dict, List, Optional

import jellyfish
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from config import (
    ADDRESS_MATCH_ALGO,
    BROKEN_PERIOD_INTEREST_TOLERANCE_PCT,
    DOC_TYPE_ALIASES,
    FIELD_ALIASES,
    FUZZY_MATCH_BAND,
    FUZZY_PARTIAL_LOWER,
    LOAN_AMOUNT_THRESHOLD_PCT,
    NAME_MATCH_ALGO,
    get_canonical_doc_type,
)
from pipeline.engines.llm_adjudicator import llm_adjudicate

logger = logging.getLogger("disbursement_pipeline.comparison")

# Backward compatibility alias
DOC_ALIASES = DOC_TYPE_ALIASES


def resolve_doc_data(extracted_data: dict[str, Any], doc_type: str) -> dict[str, Any] | None:
    """Finds extracted document data by checking canonical key and known aliases."""
    if not extracted_data:
        return None
    canonical = get_canonical_doc_type(doc_type)
    candidate_keys = [canonical] + DOC_TYPE_ALIASES.get(canonical, [])
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

    date_formats = [
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%Y/%m/%d",
        "%d.%m.%Y",
        "%d %b %Y",
        "%d %B %Y",
        "%d%m%Y",
        "%Y%m%d",
    ]
    for fmt in date_formats:
        try:
            dt = datetime.datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

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


def clean_aadhaar(val: Any) -> str:
    """Normalizes Aadhaar string by removing whitespace, dashes, and dots."""
    if val is None:
        return ""
    return re.sub(r"[\s\-_.]", "", str(val)).upper()


def is_masked_aadhaar(val: Any) -> bool:
    """Detects whether an Aadhaar representation is masked."""
    s = clean_aadhaar(val)
    if not s:
        return False
    return bool(re.search(r"[X*•]", s))


def mask_aadhaar(val: Any, mask_char: str = "X", group_format: bool = False) -> str | None:
    """Masks an Aadhaar number to standard format (e.g. XXXXXXXX1234 or XXXX XXXX 1234)."""
    s = clean_aadhaar(val)
    if not s:
        return None
    digits = re.findall(r"\d", s)
    if len(digits) >= 4:
        last4 = "".join(digits[-4:])
    else:
        return None
    if group_format:
        return f"{mask_char * 4} {mask_char * 4} {last4}"
    return f"{mask_char * 8}{last4}"


def compare_aadhaar(val_a: Any, val_b: Any) -> tuple[bool, str, float]:
    """Compares two Aadhaar numbers supporting masked and unmasked variations."""
    clean_a = clean_aadhaar(val_a)
    clean_b = clean_aadhaar(val_b)
    if not clean_a or not clean_b:
        return False, f"Missing Aadhaar value: '{val_a}' vs '{val_b}'", 0.0

    digits_a = re.findall(r"\d", clean_a)
    digits_b = re.findall(r"\d", clean_b)
    last4_a = "".join(digits_a[-4:]) if len(digits_a) >= 4 else None
    last4_b = "".join(digits_b[-4:]) if len(digits_b) >= 4 else None

    is_masked = is_masked_aadhaar(clean_a) or is_masked_aadhaar(clean_b)

    if is_masked:
        if last4_a and last4_b and last4_a == last4_b:
            return True, f"Masked Aadhaar match (ending in {last4_a})", 1.0
        return False, f"Masked Aadhaar mismatch: '{clean_a}' vs '{clean_b}'", 0.0

    # Neither is masked
    if clean_a == clean_b:
        return True, "Exact Aadhaar match", 1.0

    # If both have 12 digits but do not match
    if len(digits_a) == 12 and len(digits_b) == 12:
        return False, f"Aadhaar number mismatch: {clean_a} vs {clean_b}", 0.0

    # If one is 4 digits and other is 12 digits
    if last4_a and last4_b and last4_a == last4_b and (len(clean_a) == 4 or len(clean_b) == 4):
        return True, f"Aadhaar partial last-4 match ({last4_a})", 0.95

    return False, f"Aadhaar mismatch: '{clean_a}' vs '{clean_b}'", 0.0


def normalize_tenure_months(val: Any) -> int | None:
    """Normalizes tenure representations (e.g. 36, '36 months', '3 years') to integer months."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        try:
            i_val = int(round(float(val)))
            return i_val if i_val > 0 else None
        except (ValueError, OverflowError):
            return None

    s = str(val).strip().lower()
    if not s:
        return None

    # Check if specified in years e.g. "3 years", "3 yrs", "3 yr", "3.0 years", "3y"
    year_match = re.search(r"^(\d+(?:\.\d+)?)\s*(?:year|yr|y)s?$", s)
    if year_match:
        try:
            years = float(year_match.group(1))
            return int(round(years * 12))
        except ValueError:
            pass

    # Look for general month string or integer digits e.g. "36", "36 months", "36m", "36 mons"
    m = re.search(r"(\d+)", s)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def compare_tenure(val_a: Any, val_b: Any) -> tuple[bool, str, float]:
    """Compares two tenure values after normalizing to months."""
    t_a = normalize_tenure_months(val_a)
    t_b = normalize_tenure_months(val_b)
    if t_a is None or t_b is None:
        return False, f"Could not parse tenure: '{val_a}' vs '{val_b}'", 0.0
    if t_a == t_b:
        return True, f"Tenure matches ({t_a} months)", 1.0
    return False, f"Tenure mismatch: {t_a} months vs {t_b} months", 0.0


def extract_field_value(doc_dict: dict[str, Any], field_name: str, aliases: list[str] | None = None) -> Any:
    """Retrieves value from document dictionary checking canonical field name and aliases."""
    if not doc_dict or not isinstance(doc_dict, dict):
        return None
    if field_name in doc_dict and doc_dict[field_name] is not None:
        return doc_dict[field_name]
    lookup_aliases = list(aliases) if aliases else []
    for a in FIELD_ALIASES.get(field_name, []):
        if a not in lookup_aliases:
            lookup_aliases.append(a)
    for alias in lookup_aliases:
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
                "values": [None, extract_field_value(los_data, los_field, aliases) if los_field else None],
                "match_type": _get_match_type_label(method),
                "match_status": "NOT_FOUND",
                "confidence": 0.0,
                "method": method,
                "llm_used": False,
                "notes": f"Document '{doc_type}' not found in extracted data",
            })
            continue

        raw_doc_val = extract_field_value(doc_data, doc_field, aliases)
        raw_los_val = extract_field_value(los_data, los_field, aliases) if los_field else None

        # Binary presence-only check (e.g. customer_consent)
        if method == "presence_only":
            records.append(_evaluate_presence(check_id, subnode_name, doc_type, doc_field, raw_doc_val))
            continue

        # Missing field in either doc or LOS
        if raw_doc_val is None or (los_field and raw_los_val is None):
            if is_optional:
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
    if method in ("masked_aadhaar",):
        return "masked_id"
    if method in ("tenure_months",):
        return "tenure"
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

    if method == "exact_numeric":
        n_doc = clean_numeric(raw_doc_val)
        n_los = clean_numeric(raw_los_val)
        if n_doc is None or n_los is None:
            return {
                "check_id": check_id,
                "subnode": subnode_name,
                "field": doc_field,
                "sources": [doc_type, "los"],
                "values": [n_doc, n_los],
                "match_type": "exact_numeric",
                "match_status": "NOT_FOUND",
                "confidence": 0.0,
                "method": "exact_numeric",
                "llm_used": False,
                "notes": f"Could not parse numeric value: {raw_doc_val} vs {raw_los_val}",
            }
        is_match = abs(n_doc - n_los) < 0.01
        return {
            "check_id": check_id,
            "subnode": subnode_name,
            "field": doc_field,
            "sources": [doc_type, "los"],
            "values": [n_doc, n_los],
            "match_type": "exact_numeric",
            "match_status": "MATCH" if is_match else "MISMATCH",
            "confidence": 1.0 if is_match else 0.0,
            "method": "exact_numeric",
            "llm_used": False,
            "notes": None if is_match else f"Numeric mismatch: {n_doc} vs {n_los}",
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

    if method == "masked_aadhaar" or doc_field in ("aadhaar_number", "aadhaar_no", "aadhaar", "uid"):
        is_match, note, conf = compare_aadhaar(raw_doc_val, raw_los_val)
        return {
            "check_id": check_id,
            "subnode": subnode_name,
            "field": doc_field,
            "sources": [doc_type, "los"],
            "values": [raw_doc_val, raw_los_val],
            "match_type": "masked_id",
            "match_status": "MATCH" if is_match else "MISMATCH",
            "confidence": conf,
            "method": "masked_aadhaar_compare",
            "llm_used": False,
            "notes": None if is_match else note,
        }

    if method == "tenure_months" or doc_field in ("loan_validity", "tenure", "tenure_months", "loan_term", "term"):
        is_match, note, conf = compare_tenure(raw_doc_val, raw_los_val)
        return {
            "check_id": check_id,
            "subnode": subnode_name,
            "field": doc_field,
            "sources": [doc_type, "los"],
            "values": [raw_doc_val, raw_los_val],
            "match_type": "tenure",
            "match_status": "MATCH" if is_match else "MISMATCH",
            "confidence": conf,
            "method": "tenure_months_compare",
            "llm_used": False,
            "notes": None if is_match else note,
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

    Optional: If neither document contains BPI, returns None.
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
