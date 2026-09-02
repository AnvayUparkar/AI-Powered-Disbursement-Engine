import json
import logging
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.serializers.case_serializer import serialize_all_cases
from config import S3_RESULT_DIR
from pipeline.audit import append_audit_entry
from pipeline.state import compute_rollup
from pipeline.storage import read_json, write_json

logger = logging.getLogger("disbursement_pipeline.api.reviews")


router = APIRouter(prefix="/api/reviews", tags=["Human Review"])

CP_TO_CHECK_IDS: dict[int, list[str]] = {
    1: ["chk_loan_amt_application_form_vs_kfs", "chk_kfs_vs_los_funding", "chk_sanction_vs_los_funding"],
    2: ["chk_loan_validity_tenure"],
    3: ["chk_app_form_signature"],
    4: ["chk_pan_number", "chk_kyc_name_pan", "chk_applicant_name_app_vs_pan", "chk_current_address_app_vs_kyc", "chk_permanent_address_app_vs_kyc"],
    5: ["chk_face_similarity_selfie"],
    6: ["chk_loan_agreement_digital_signature", "chk_loan_agreement_otp_consent"],
    7: ["chk_kfs_vs_los_funding"],
    8: ["chk_sanction_vs_los_funding"],
    9: ["chk_aadhaar_xml_mandatory_presence"],
    10: ["chk_broken_period_interest_split"],
    11: ["chk_disbursal_memo_application_id", "chk_disbursal_memo_amount_threshold"],
    12: ["chk_bt_closure_vs_final_fc"],
}


class AdjudicationRequest(BaseModel):
    decision: str  # "APPROVE" | "REJECT" | "OVERRIDE"
    notes: str | None = None
    assignedTo: str | None = "Current Reviewer"


def _generate_review_items() -> list[dict]:
    """Extracts review items across all cases from INDETERMINATE or DISCREPANCY checkpoints."""
    all_cases = serialize_all_cases()
    reviews = []

    for c in all_cases:
        for cp in c["checkpoints"]:
            if cp["status"] in ("INDETERMINATE", "DISCREPANCY"):
                review_id = f"rev-{c['id']}-{cp['id']}"
                first_field = cp["extractedFields"][0] if cp["extractedFields"] else None
                reviews.append({
                    "id": review_id,
                    "caseId": c["id"],
                    "issue": cp["reason"],
                    "checkpointName": cp["name"],
                    "checkpointId": cp["id"],
                    "confidence": cp["confidence"],
                    "priority": "HIGH" if cp["status"] == "DISCREPANCY" else "MEDIUM",
                    "createdAt": c["lastUpdated"],
                    "assignedTo": None,
                    "documentId": first_field["sourceDocumentId"] if first_field else f"doc-{c['id']}",
                    "fieldName": first_field["name"] if first_field else cp["name"],
                    "extractedValue": str(first_field["value"]) if first_field else "—",
                    "systemRecommendation": "DISCREPANCY" if cp["status"] == "DISCREPANCY" else "REVIEW",
                })

    return reviews


@router.get("", summary="List human review queue items")
def list_reviews(
    caseId: str | None = Query(None, description="Filter by case ID"),
    priority: str | None = Query(None, description="Filter by priority (LOW, MEDIUM, HIGH, ALL)"),
    status: str | None = Query(None),
):
    items = _generate_review_items()
    if caseId:
        items = [r for r in items if r["caseId"] == caseId]
    if priority and priority != "ALL":
        items = [r for r in items if r["priority"] == priority]
    return items


@router.get("/{review_id}", summary="Get single review item")
def get_review(review_id: str):
    items = _generate_review_items()
    item = next((r for r in items if r["id"] == review_id), None)
    if not item:
        raise HTTPException(status_code=404, detail=f"Review item not found: {review_id}")
    return item


@router.post("/{review_id}/adjudicate", summary="Submit human adjudication decision")
def adjudicate_review(review_id: str, req: AdjudicationRequest):
    items = _generate_review_items()
    item = next((r for r in items if r["id"] == review_id), None)
    if not item:
        raise HTTPException(status_code=404, detail=f"Review item not found: {review_id}")

    loan_id = item["caseId"]
    cp_id = item["checkpointId"]
    decision = req.decision.upper()
    assigned_to = req.assignedTo or "Reviewer"

    # Persist the change directly to result artifacts
    res_dir = S3_RESULT_DIR / loan_id
    comp_file = res_dir / "comparison_results.json"
    rollups_file = res_dir / "subnode_rollups.json"
    scorecard_file = res_dir / "scorecard.json"

    updated_status = "MATCH" if decision in ("APPROVE", "OVERRIDE") else "MISMATCH"
    target_check_ids = CP_TO_CHECK_IDS.get(int(cp_id), []) if isinstance(cp_id, int) or str(cp_id).isdigit() else [str(cp_id)]

    records: list[dict[str, Any]] = []
    if comp_file.exists():
        try:
            records = read_json(comp_file)
        except (json.JSONDecodeError, OSError):
            records = []

    matched_count = 0
    for r in records:
        check_id = r.get("check_id")
        if check_id in target_check_ids or (not target_check_ids and str(check_id) == str(cp_id)):
            r["match_status"] = updated_status
            r["confidence"] = 1.0
            r["notes"] = f"Human adjudication [{decision}] by {assigned_to}: {req.notes or ''}".strip()
            r["adjudicated_by"] = assigned_to
            matched_count += 1

    # Recalculate subnode rollups
    subnode_records: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        sn = r.get("subnode", "general")
        subnode_records[sn].append(r)

    rollups: dict[str, str] = {}
    for sn, sn_recs in subnode_records.items():
        rollups[sn] = compute_rollup(sn_recs)

    # Save comparison_results and subnode_rollups
    if records:
        write_json(comp_file, records)
    if rollups:
        write_json(rollups_file, rollups)

    # Update scorecard
    if scorecard_file.exists():
        try:
            sc = read_json(scorecard_file)
            sc["subnode_rollups"] = rollups
            has_discrepancy = any(v == "Discrepancy" for v in rollups.values())
            has_indeterminate = any(v == "Indeterminate" for v in rollups.values())
            if has_discrepancy:
                sc["preliminary_decision"] = "REJECT_OR_FLAG"
            elif has_indeterminate:
                sc["preliminary_decision"] = "MANUAL_REVIEW"
            else:
                sc["preliminary_decision"] = "AUTO_APPROVE_ELIGIBLE"
            write_json(scorecard_file, sc)
        except (json.JSONDecodeError, OSError, KeyError) as e:
            logger.warning("Could not update scorecard for %s after adjudication: %s", loan_id, e)


    # Append audit log entry
    audit_entry = {
        "type": "human_adjudication_decision",
        "review_id": review_id,
        "checkpoint_id": item["checkpointId"],
        "checkpoint_name": item["checkpointName"],
        "decision": decision,
        "notes": req.notes,
        "adjudicated_by": assigned_to,
        "records_updated": matched_count,
        "new_rollups": rollups,
    }
    append_audit_entry(loan_id, audit_entry)
    logger.info("Human adjudication saved for %s / %s: %s (updated %d records)", loan_id, review_id, decision, matched_count)

    return {
        "status": "success",
        "review_id": review_id,
        "decision": decision,
        "records_updated": matched_count,
        "audit_logged": True,
    }

