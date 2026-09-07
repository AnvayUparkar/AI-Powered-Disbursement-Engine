"""Node: Generate Scorecard — Evaluates 12 DGSC Checkpoints and computes disbursement scorecard."""
import logging
from typing import Any, Dict, List

from config import CHECKPOINTS_SPEC
from pipeline.state import PipelineState
from pipeline.storage import save_s3_result, update_status

logger = logging.getLogger("disbursement_pipeline.generate_scorecard")


def _evaluate_checkpoints(comparison_results: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], float]:
    """Maps comparison results to the 12 DGSC Checkpoints and calculates overall score."""
    records_by_field: Dict[str, List[Dict[str, Any]]] = {}
    for r in comparison_results:
        f = r.get("field", "")
        if f not in records_by_field:
            records_by_field[f] = []
        records_by_field[f].append(r)

    checkpoints: List[Dict[str, Any]] = []
    total_weight = 0.0
    earned_weight = 0.0

    from config import FIELD_CRITICALITY_WEIGHTS

    for spec in CHECKPOINTS_SPEC:
        cp_id = spec["id"]
        cp_name = spec["name"]
        weight = float(spec.get("weight", 10.0))
        total_weight += weight

        matched_records: List[Dict[str, Any]] = []
        for field in spec["fields"]:
            if field in records_by_field:
                matched_records.extend(records_by_field[field])

        if not matched_records:
            status = "INDETERMINATE"
            confidence = 0.0
            score_fraction = 0.0
            match_score = 0.0
            reason = f"No documents provided for required fields: {', '.join(spec['fields'])}"
        else:
            statuses = {r.get("match_status") for r in matched_records}
            confidences = [float(r.get("confidence") or 0.0) for r in matched_records]
            avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

            total_field_weight = 0.0
            earned_field_weight = 0.0
            for r in matched_records:
                fld = r.get("field", "")
                fw = FIELD_CRITICALITY_WEIGHTS.get(fld, 1.0)
                total_field_weight += fw
                ms = r.get("match_status")
                if ms == "MATCH":
                    earned_field_weight += fw
                elif ms in ("PARTIAL", "FUZZY"):
                    earned_field_weight += fw * 0.5

            field_match_ratio = (earned_field_weight / total_field_weight) if total_field_weight > 0 else 0.0
            match_score = round(field_match_ratio * 100, 1)

            if "MISMATCH" in statuses:
                status = "DISCREPANCY"
                confidence = round(avg_conf * 100, 1) if avg_conf > 0 else match_score
                score_fraction = round(field_match_ratio, 3)
                mismatch_notes = [r.get("notes") for r in matched_records if r.get("notes") and r.get("match_status") == "MISMATCH"]
                reason = mismatch_notes[0] if mismatch_notes else "One or more fields failed verification."
            elif statuses & {"PARTIAL", "NOT_FOUND"}:
                status = "INDETERMINATE"
                confidence = round(avg_conf * 100, 1) if avg_conf > 0 else match_score
                score_fraction = round(field_match_ratio, 3)
                reason = "Borderline or missing fields require manual check."
            else:
                status = "VERIFIED"
                confidence = round(avg_conf * 100, 1) if avg_conf > 0 else 98.0
                score_fraction = 1.0
                reason = "All required fields successfully verified against records."

        earned_weight += weight * score_fraction

        checkpoints.append({
            "id": cp_id,
            "name": cp_name,
            "category": spec.get("category", "General"),
            "status": status,
            "confidence": confidence,
            "match_score": match_score,
            "weight": weight,
            "reason": reason,
            "rule": spec["rule"],
        })

    final_score = round((earned_weight / total_weight) * 100, 1) if total_weight > 0 else 0.0
    return checkpoints, final_score


def generate_scorecard(state: PipelineState) -> PipelineState:
    """Calculates DGSC 12-checkpoint scorecard, preliminary decision, and persists to S3 Result."""
    loan_id = state["loan_id"]
    errors = list(state.get("errors", []))
    history = list(state.get("node_history", []))
    history.append("generate_scorecard")

    logger.info("Executing generate_scorecard for loan: %s", loan_id)

    comparison_results = state.get("comparison_results", [])
    subnode_rollups = state.get("subnode_rollups", {})

    checkpoints, score = _evaluate_checkpoints(comparison_results)

    has_discrepancy = any(c["status"] == "DISCREPANCY" for c in checkpoints)
    has_indeterminate = any(c["status"] == "INDETERMINATE" for c in checkpoints)

    if has_discrepancy or score < 70.0:
        preliminary_decision = "REJECT_OR_FLAG"
        risk_tier = "HIGH_RISK"
    elif has_indeterminate or score < 90.0:
        preliminary_decision = "MANUAL_REVIEW"
        risk_tier = "MEDIUM_RISK"
    else:
        preliminary_decision = "AUTO_APPROVE_ELIGIBLE"
        risk_tier = "LOW_RISK"

    scorecard = {
        "loan_id": loan_id,
        "overall_score": score,
        "preliminary_decision": preliminary_decision,
        "risk_tier": risk_tier,
        "scoring_status": "COMPLETED",
        "subnode_rollups": subnode_rollups,
        "checkpoints": checkpoints,
        "notes": f"Automated 12-checkpoint DGSC scoring completed with overall score {score}% ({risk_tier}).",
    }

    save_s3_result(loan_id, "scorecard.json", scorecard)
    update_status(loan_id, current_node="generate_scorecard", errors=errors, node_history=history)

    return {
        **state,
        "scorecard": scorecard,
        "errors": errors,
        "node_history": history,
    }
