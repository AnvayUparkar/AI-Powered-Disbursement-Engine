"""Document type detection, name normalization, and synthetic alias resolution."""
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.doc_types import get_canonical_doc_type, get_display_name


def guess_doc_type(filename: str) -> str:
    """Infer human-readable document type from filename or canonical mapping."""
    try:
        return get_display_name(filename)
    except Exception:
        pass

    n = filename.lower()
    if "app" in n or "application" in n:
        return "Application Form"
    if "pan" in n:
        return "PAN"
    if ("aadhaar" in n or "aadhar" in n or "adhar" in n) and "xml" in n:
        return "Aadhaar XML"
    if "aadhaar" in n or "aadhar" in n or "adhar" in n:
        return "Aadhaar"
    if "kyc" in n:
        return "KYC"
    if "kfs" in n:
        return "KFS"
    if "sanction" in n:
        return "Sanction Letter"
    if "agreement" in n:
        return "Loan Agreement"
    if "memo" in n or "disbursal" in n:
        return "Disbursal Memo"
    if "bt" in n or "foreclosure" in n:
        return "BT Details"
    if "vkyc" in n:
        return "VKYC Audit Trail"
    return "Miscellaneous"


def normalize_doc_name(name: str) -> str:
    """Normalize document name for consistent identity and deduplication."""
    clean_name = (name or "").lower()
    base_name = re.sub(r"^doc-[a-z0-9_\-]+_", "", clean_name)
    norm_name = re.sub(r"^(aadhaar|aadhar|adhar)[_\s\-]+", "", base_name)
    norm_name = norm_name.replace("aadhaar", "aadhar").replace("adhar", "aadhar")
    return norm_name


def resolve_synthetic_alias(
    doc_id: str,
    candidate_docs: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    Resolve synthetic document ID references (e.g. 'doc-LOAN_001-sanction')
    against candidate document records using canonical document types and substring matching.
    """
    if not doc_id or not doc_id.startswith("doc-"):
        return None

    parts = doc_id.split("-", 2)
    if len(parts) != 3:
        return None

    target_case = parts[1].strip()
    target_slug = parts[2].lower().strip()

    try:
        target_canon = get_canonical_doc_type(target_slug)
    except Exception:
        target_canon = "miscellaneous"

    for d in candidate_docs:
        if str(d.get("caseId", "")).upper() == target_case.upper():
            d_canon = target_canon
            try:
                d_canon = get_canonical_doc_type(d.get("name") or d.get("type") or "")
            except Exception:
                pass

            clean_type = str(d.get("type") or "").lower().replace(" ", "_")
            clean_name = str(d.get("name") or "").lower()

            if (
                (target_canon != "miscellaneous" and d_canon == target_canon)
                or target_slug in clean_name
                or target_slug in clean_type
            ):
                return d

    return None
