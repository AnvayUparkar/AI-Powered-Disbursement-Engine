"""Case context and primitive building blocks for loan case serialization."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import FIELD_CRITICALITY_WEIGHTS, IST


def format_time_12h(dt_or_str: datetime | str | None) -> str:
    """Formats datetime or time string into 12-hour format: '3:13 pm'."""
    if not dt_or_str:
        return ""
    dt: datetime | None = None
    if isinstance(dt_or_str, datetime):
        dt = dt_or_str
    elif isinstance(dt_or_str, str):
        val = dt_or_str.strip()
        if re.match(r"^\d{1,2}:\d{2}\s?(am|pm)$", val, re.I):
            parts = val.split()
            return f"{parts[0]} {parts[1].lower()}" if len(parts) == 2 else val.lower()
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt = dt.astimezone(IST)
        except (ValueError, TypeError):
            for fmt in ("%H:%M:%S", "%H:%M"):
                try:
                    parsed_t = datetime.strptime(val, fmt).time()
                    now_ist = datetime.now(IST)
                    dt = datetime.combine(now_ist.date(), parsed_t, tzinfo=IST)
                    break
                except ValueError:
                    continue
    if not dt:
        return str(dt_or_str)

    hour = dt.strftime("%I").lstrip("0") or "12"
    minute_meridiem = dt.strftime("%M %p").lower()
    return f"{hour}:{minute_meridiem}"


def format_date_dmy(val: datetime | str | None) -> str | None:
    """Formats datetime or date string into DD/MM/YYYY."""
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val.strftime("%d/%m/%Y")
    if isinstance(val, str):
        v = val.strip()
        if re.match(r"^\d{2}/\d{2}/\d{4}$", v):
            return v
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y"):
            try:
                d = datetime.strptime(v.split("T")[0], fmt)
                return d.strftime("%d/%m/%Y")
            except ValueError:
                continue
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return d.strftime("%d/%m/%Y")
        except (ValueError, TypeError):
            pass
    return str(val)


def format_datetime_dmy_12h(val: datetime | str | None) -> str:
    """Formats datetime into 'DD/MM/YYYY, 3:13 pm'."""
    if val is None or val == "":
        now = datetime.now(IST)
        return f"{now.strftime('%d/%m/%Y')}, {format_time_12h(now)}"
    dt: datetime | None = None
    if isinstance(val, datetime):
        dt = val
    elif isinstance(val, str):
        v = val.strip()
        try:
            parsed = datetime.fromisoformat(v.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            dt = parsed.astimezone(IST)
        except (ValueError, TypeError):
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
                try:
                    dt = datetime.strptime(v, fmt).replace(tzinfo=IST)
                    break
                except ValueError:
                    continue
    if not dt:
        now = datetime.now(IST)
        return f"{now.strftime('%d/%m/%Y')}, {format_time_12h(now)}"

    date_part = dt.strftime("%d/%m/%Y")
    time_part = format_time_12h(dt)
    return f"{date_part}, {time_part}"


from pipeline.engines.comparison import clean_numeric


def safe_float(val: Any, default: float = 0.0) -> float:
    """Safely parses a float from string/int/float, stripping commas, currency symbols, etc."""
    num = clean_numeric(val)
    return float(num) if num is not None else default


def inr_format(val: Any) -> str:
    """Formats numeric value to INR string with comma grouping."""
    if val is None or val == "":
        return "₹0"
    num = clean_numeric(val)
    if num is None:
        return "₹0"
    return f"₹{int(num):,}"


def format_tenure_months(val: Any) -> str:
    """Formats a tenure value into 'X Months' without duplicating month units."""
    if val is None or val == "":
        return "N/A"
    s = str(val).strip()
    if not s or s.lower() in ("none", "null", "n/a"):
        return "N/A"
    if "month" in s.lower():
        return re.sub(r"(?i)(\s*\bmonths?\b)+", " Months", s).strip()
    return f"{s} Months"



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


def resolve_checkpoint_validation(
    status: str,
    default_left: str,
    default_right: str,
    records: list[dict[str, Any]] | None = None,
    default_left_source: str | None = None,
    default_right_source: str | None = None,
    fallback_result: str | None = None,
) -> dict[str, Any]:
    """Resolves a validation block enforcing production invariants:
    - If status == 'DISCREPANCY', bind left/right to the primary failing check's values & sources.
    - Identity invariant: left == right NEVER produces result == 'MISMATCH'.
    - Symmetrical missing states (N/A vs N/A) are mapped to semantic indicators or INCONCLUSIVE.
    """
    mismatched_check = None
    if records:
        for r in records:
            if isinstance(r, dict) and (r.get("match_status") == "MISMATCH" or r.get("result") == "MISMATCH"):
                mismatched_check = r
                break

    if status == "DISCREPANCY" and mismatched_check is not None:
        vals = mismatched_check.get("values") or []
        srcs = mismatched_check.get("sources") or []
        left_val = str(vals[0]) if len(vals) > 0 and vals[0] is not None else default_left
        right_val = str(vals[1]) if len(vals) > 1 and vals[1] is not None else default_right
        left_src = str(srcs[0]) if len(srcs) > 0 and srcs[0] else default_left_source
        right_src = str(srcs[1]) if len(srcs) > 1 and srcs[1] else default_right_source

        # If values happen to be identical (e.g. format nuance), ensure left != right
        result = "MISMATCH" if left_val != right_val else "MATCH"
        val_block: dict[str, Any] = {
            "left": left_val,
            "right": right_val,
            "result": result,
        }
        if left_src:
            val_block["leftSource"] = left_src
        if right_src:
            val_block["rightSource"] = right_src
        return val_block

    if status == "VERIFIED" or status == "NOT_APPLICABLE":
        result = "MATCH"
    elif status == "DISCREPANCY":
        # Ensure identity invariant: if left == right, do not display MISMATCH
        result = "MISMATCH" if default_left != default_right else "MATCH"
    else:  # INDETERMINATE
        if fallback_result is not None:
            result = fallback_result
        elif default_left in ("N/A", "Missing") and default_right in ("N/A", "Missing"):
            result = "INCONCLUSIVE"
        elif default_left == default_right:
            result = "MATCH"
        else:
            result = "MISMATCH"

    val_block = {
        "left": default_left,
        "right": default_right,
        "result": result,
    }
    if default_left_source:
        val_block["leftSource"] = default_left_source
    if default_right_source:
        val_block["rightSource"] = default_right_source
    return val_block


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
    comparisons: list[dict[str, Any]] | None = None,
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
        "comparisons": comparisons or [],
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
