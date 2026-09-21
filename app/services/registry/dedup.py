"""Deduplication and query filtering for document registry records."""
from typing import Any, Dict, List, Optional, Set

from .resolver import normalize_doc_name

SINGLETON_CANONICAL_TYPES: Set[str] = {
    "application_form",
    "aadhaar",
    "pan",
    "sanction_letter",
    "loan_agreement",
    "disbursal_memo",
    "kfs",
    "aadhaar_xml",
}

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
    Prevents duplicates using canonical singleton document types and normalized filenames.
    """
    from config.doc_types import get_canonical_doc_type

    seen_keys: Set[Any] = set()
    all_docs: List[Dict[str, Any]] = []

    # Dynamic uploaded documents take precedence and appear first (most recent first)
    dynamic_list = list(reversed(dynamic_docs))
    for d in dynamic_list:
        c_id = d.get("caseId")
        dtype = d.get("type") or ""
        dname = d.get("name") or ""
        doc_id = d.get("id") or dname
        canon = get_canonical_doc_type(dtype or dname)
        norm_name = normalize_doc_name(dname)

        is_dup = False
        if (c_id, doc_id) in seen_keys or (c_id, dname) in seen_keys or (c_id, norm_name) in seen_keys:
            is_dup = True
        elif c_id and c_id != "GENERAL" and canon in SINGLETON_CANONICAL_TYPES and (c_id, canon) in seen_keys:
            is_dup = True

        if is_dup:
            continue

        seen_keys.add((c_id, doc_id))
        seen_keys.add((c_id, dname))
        seen_keys.add((c_id, norm_name))
        if c_id and c_id != "GENERAL" and canon != "miscellaneous":
            seen_keys.add((c_id, canon))

        all_docs.append(d)

    for d in case_docs:
        c_id = d.get("caseId")
        dtype = d.get("type") or ""
        dname = d.get("name") or ""
        doc_id = d.get("id") or dname
        canon = get_canonical_doc_type(dtype or dname)
        norm_name = normalize_doc_name(dname)

        is_dup = False
        if (c_id, doc_id) in seen_keys or (c_id, dname) in seen_keys or (c_id, norm_name) in seen_keys:
            is_dup = True
        elif c_id and c_id != "GENERAL" and canon in SINGLETON_CANONICAL_TYPES and (c_id, canon) in seen_keys:
            is_dup = True

        if is_dup:
            if d.get("ocrStatus") == "COMPLETED":
                for existing in all_docs:
                    if existing.get("caseId") == c_id and (
                        existing.get("name") == dname
                        or normalize_doc_name(existing.get("name", "")) == norm_name
                        or (canon in SINGLETON_CANONICAL_TYPES and get_canonical_doc_type(existing.get("type", "") or existing.get("name", "")) == canon)
                    ):
                        if existing.get("ocrStatus") != "COMPLETED":
                            for k in ("ocrStatus", "extractionStatus", "status", "confidence", "rawText", "formattedText", "extractedFields", "pages", "debug"):
                                if d.get(k):
                                    existing[k] = d[k]
                        break
            continue

        seen_keys.add((c_id, doc_id))
        seen_keys.add((c_id, dname))
        seen_keys.add((c_id, norm_name))
        if c_id and c_id != "GENERAL" and canon != "miscellaneous":
            seen_keys.add((c_id, canon))

        all_docs.append(d)

    # Sort youngest (newest upload) to oldest
    all_docs.sort(
        key=lambda x: (x.get("uploadedTimestamp") or 0.0, x.get("uploadedAt") or ""),
        reverse=True,
    )

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
        if case_id != "GENERAL":
            from config import DMS_DIR, S3_RAW_DIR
            filtered = [
                d for d in filtered
                if d.get("caseId") == case_id
                and (
                    (d.get("uploadedTimestamp") or 0.0) > 0.0
                    or (S3_RAW_DIR / case_id / (d.get("name") or "")).exists()
                    or (DMS_DIR / case_id / (d.get("name") or "")).exists()
                )
            ]
        else:
            filtered = [d for d in filtered if d.get("caseId") == "GENERAL" or not d.get("caseId")]

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
