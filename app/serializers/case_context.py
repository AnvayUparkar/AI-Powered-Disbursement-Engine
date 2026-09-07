"""Case context and primitive building blocks for loan case serialization."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config import FIELD_CRITICALITY_WEIGHTS


def inr_format(val: float | None) -> str:
    """Formats numeric value to INR string with comma grouping."""
    if val is None:
        return "₹0"
    return f"₹{int(val):,}"


def build_evidence(
    doc_id: str,
    doc_name: str,
    label: str,
    page: int = 1,
    field: str | None = None,
) -> dict[str, Any]:
    """Builds an evidence snippet record for a checkpoint."""
    return {
        "id": f"ev-{doc_id}-{field or 'field'}",
        "label": label,
        "documentId": doc_id,
        "documentName": doc_name,
        "page": page,
        "field": field,
    }


def build_field(
    name: str,
    value: Any,
    confidence: float,
    doc_id: str,
    page: int = 1,
) -> dict[str, Any]:
    """Builds an extracted field element for a checkpoint."""
    return {
        "id": f"fld-{name.lower().replace(' ', '_')}",
        "name": name,
        "value": value,
        "confidence": round(confidence, 1),
        "sourceDocumentId": doc_id,
        "page": page,
    }


def build_checkpoint(
    cp_id: int,
    name: str,
    status: str,
    confidence: float,
    reason: str,
    rule: str,
    fields: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    validation: dict[str, Any] | None = None,
    match_score: float | None = None,
) -> dict[str, Any]:
    """Builds a standardized checkpoint record."""
    cp_data: dict[str, Any] = {
        "id": cp_id,
        "name": name,
        "status": status,
        "confidence": round(confidence, 1),
        "reason": reason,
        "rule": rule,
        "extractedFields": fields,
        "evidence": evidence,
        "validation": validation,
    }
    if match_score is not None:
        cp_data["matchScore"] = round(match_score, 1)
        cp_data["match_score"] = round(match_score, 1)
    return cp_data


def compute_checkpoint_confidence(
    fields: list[dict[str, Any]],
    records: list[dict[str, Any]] | None = None,
    default_conf: float = 95.0,
) -> float:
    """Calculates weighted confidence dynamically from extracted fields and comparison records."""
    confidences: list[float] = []
    weights: list[float] = []

    if records:
        for r in records:
            if isinstance(r, dict):
                c = r.get("confidence")
                if c is not None:
                    try:
                        c_val = float(c)
                        if c_val <= 1.0:
                            c_val *= 100.0
                        fld = r.get("field", "")
                        w = FIELD_CRITICALITY_WEIGHTS.get(fld, 1.0)
                        confidences.append(c_val)
                        weights.append(w)
                    except (ValueError, TypeError):
                        pass

    if not confidences and fields:
        for f in fields:
            if isinstance(f, dict):
                c = f.get("confidence")
                if c is not None:
                    try:
                        c_val = float(c)
                        if c_val <= 1.0:
                            c_val *= 100.0
                        fld_name = f.get("name", "").lower().replace(" ", "_")
                        w = FIELD_CRITICALITY_WEIGHTS.get(fld_name, 1.0)
                        confidences.append(c_val)
                        weights.append(w)
                    except (ValueError, TypeError):
                        pass

    if not confidences:
        return default_conf

    total_w = sum(weights)
    if total_w > 0:
        return sum(c * w for c, w in zip(confidences, weights)) / total_w
    return sum(confidences) / len(confidences)


@dataclass(frozen=True)
class CaseContext:
    """Encapsulates all loaded artifacts, extracted documents, and metadata for a single case."""

    loan_id: str
    los_data: dict[str, Any]
    docs: dict[str, dict[str, Any]]
    real_doc_names: list[str]
    doc_ids: list[str]
    records: list[dict[str, Any]]
    records_by_id: dict[str, dict[str, Any]]
    records_by_field: dict[str, list[dict[str, Any]]]
    records_by_subnode: dict[str, list[dict[str, Any]]]
    status_data: dict[str, Any]
    scorecard_data: dict[str, Any]
    subnode_rollups: dict[str, Any]
    loan_amount: float
    disbursal_amount: float
    applicant_name: str
    app_id: str
    loan_type: str
    is_bt: bool
    raw_dir: Path
    dms_dir: Path
    extracted_dir: Path
    extracted_structured_dir: Path
    result_dir: Path

    def get_check_record(self, *candidate_ids: str, field: str | None = None) -> dict[str, Any] | None:
        """Finds a comparison record by matching check IDs or field name."""
        for cid in candidate_ids:
            if cid and cid in self.records_by_id:
                return self.records_by_id[cid]
        if field and field in self.records_by_field:
            return self.records_by_field[field][0]
        return None

    def get_doc(self, *candidate_names: str) -> dict[str, Any]:
        """Finds a document dictionary by matching canonical or stem names."""
        for name in candidate_names:
            if name in self.docs and isinstance(self.docs[name], dict):
                return self.docs[name]
        return {}

    def has_doc_matching(self, *substrings: str) -> bool:
        """Checks if any discovered raw/DMS filename contains any of the target substrings."""
        for name in self.real_doc_names:
            name_lower = name.lower()
            if any(sub.lower() in name_lower for sub in substrings):
                return True
        return False
