"""Deduplication and query filtering for document registry records."""
from typing import Any, Dict, List, Optional, Set

from .resolver import normalize_doc_name

SINGLETON_TYPES: Set[str] = {
    "Application Form",
    "Aadhaar",
    "PAN",
    "Sanction Letter",
    "Loan Agreement",
    "Disbursal Memo",
    "KFS",
    "Aadhaar XML",
}

STANDARD_TYPES: List[str] = [
    "Application Form",
    "PAN",
    "Aadhaar",
    "KYC",
    "KFS",
    "Sanction Letter",
    "Loan Agreement",
    "Disbursal Memo",
    "BT Details",
    "Aadhaar XML",
    "VKYC Audit Trail",
    "Miscellaneous",
]


def merge_and_deduplicate(
    dynamic_docs: List[Dict[str, Any]],
    case_docs: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Merge dynamic uploaded documents and case disk documents with priority given to uploads.
    Prevents duplicates using singleton document type constraints and normalized names.
    """
    seen_keys: Set[Any] = set()
    all_docs: List[Dict[str, Any]] = []

    # Dynamic uploaded documents take precedence and appear first (most recent first)
    dynamic_list = list(reversed(dynamic_docs))
    for d in dynamic_list:
        c_id = d.get("caseId")
        dtype = d.get("type")
        doc_key = d.get("id") or d.get("name")
        key = (c_id, doc_key)

        if key in seen_keys:
            continue
        seen_keys.add(key)

        if dtype in SINGLETON_TYPES and c_id and c_id != "GENERAL":
            seen_keys.add((c_id, dtype))
        else:
            norm_name = normalize_doc_name(d.get("name", ""))
            seen_keys.add((c_id, norm_name))

        all_docs.append(d)

    for d in case_docs:
        c_id = d.get("caseId")
        dtype = d.get("type")
        if dtype in SINGLETON_TYPES and c_id and c_id != "GENERAL":
            key = (c_id, dtype)
        else:
            norm_name = normalize_doc_name(d.get("name", ""))
            key = (c_id, norm_name)

        if key in seen_keys:
            continue
        seen_keys.add(key)
        all_docs.append(d)

    return all_docs


def filter_documents(
    docs: List[Dict[str, Any]],
    case_id: Optional[str] = None,
    doc_type: Optional[str] = None,
    query: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Filter document list by case ID, doc type, and search query."""
    filtered = docs

    if case_id:
        filtered = [d for d in filtered if d.get("caseId") == case_id]

    if doc_type and doc_type != "ALL":
        filtered = [d for d in filtered if d.get("type") == doc_type]

    if query:
        q = query.lower().strip()
        filtered = [
            d for d in filtered
            if q in d.get("name", "").lower()
            or q in str(d.get("caseId", "")).lower()
            or q in str(d.get("type", "")).lower()
        ]

    return filtered


def get_all_distinct_types(dynamic_types: Set[str]) -> List[str]:
    """Combine present document types with standard system types."""
    return sorted(list(dynamic_types | set(STANDARD_TYPES)))
