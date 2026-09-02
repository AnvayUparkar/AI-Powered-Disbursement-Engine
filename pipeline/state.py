from typing import Any

from typing_extensions import TypedDict


class ResultRecord(TypedDict):
    check_id: str
    subnode: str  # "loan_kyc" | "kfs_sanction" | "topup_bt"
    field: str
    sources: list[str]
    values: list[Any]
    match_type: str  # "exact_numeric" | "exact_id" | "exact_date" | "fuzzy" | "face_similarity" | "presence" | "capture_only" | "arithmetic" | "cryptographic_verification" | "threshold"
    match_status: str  # "MATCH" | "MISMATCH" | "PARTIAL" | "NOT_FOUND" | "CAPTURED" | "NOT_IMPLEMENTED"
    confidence: float | None
    method: str
    llm_used: bool
    notes: str | None


def compute_rollup(records: list[dict]) -> str:
    """Computes the tri-state rollup status for a set of field-level records.

    - Indeterminate: When no records are provided
    - Discrepancy: Any field-level check is MISMATCH
    - Indeterminate: Any field-level check is PARTIAL / NOT_FOUND /
      NOT_IMPLEMENTED (and none are MISMATCH)
    - Verified: All field-level checks MATCH / CAPTURED
    """
    if not records:
        return "Indeterminate"

    statuses = {r["match_status"] for r in records}
    if "MISMATCH" in statuses:
        return "Discrepancy"
    if statuses & {"PARTIAL", "NOT_FOUND", "NOT_IMPLEMENTED"}:
        return "Indeterminate"
    return "Verified"


class PipelineState(TypedDict):
    loan_id: str
    los_data: dict
    raw_doc_paths: dict[str, str]
    extracted_data: dict[str, dict]
    face_embeddings: dict
    dms_status: dict
    otp_audit: dict
    comparison_results: list[dict]
    subnode_rollups: dict  # {loan_kyc, kfs_sanction, topup_bt} -> status
    compiled_report: dict
    scorecard: dict
    retry_count: int
    checker_result: dict
    errors: list[str]
    node_history: list[str]


