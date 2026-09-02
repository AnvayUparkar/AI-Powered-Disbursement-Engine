import logging

from config import S3_RESULT_DIR

from pipeline.state import PipelineState
from pipeline.storage import update_status, write_json

logger = logging.getLogger("disbursement_pipeline.node5_scorecard")


def node5_scorecard(state: PipelineState) -> PipelineState:
    """Node 5 (Scorecard) — Passthrough stub for scoring logic (not yet defined)."""
    loan_id = state["loan_id"]
    errors = list(state.get("errors", []))
    history = list(state.get("node_history", []))
    history.append("scorecard")

    logger.info("Executing Node 5 (Scorecard Passthrough) for loan %s", loan_id)

    compiled_report = state.get("compiled_report", {})
    subnode_rollups = state.get("subnode_rollups", {})

    # Overall recommendation based purely on rollups for transparency
    has_discrepancy = any(v == "Discrepancy" for v in subnode_rollups.values())
    has_indeterminate = any(v == "Indeterminate" for v in subnode_rollups.values())

    if has_discrepancy:
        preliminary_decision = "REJECT_OR_FLAG"
    elif has_indeterminate:
        preliminary_decision = "MANUAL_REVIEW"
    else:
        preliminary_decision = "AUTO_APPROVE_ELIGIBLE"

    scorecard = {
        "loan_id": loan_id,
        "scoring_status": "NOT_IMPLEMENTED",
        "notes": "Scoring algorithm logic not yet defined — passthrough compiled report",
        "preliminary_decision": preliminary_decision,
        "subnode_rollups": subnode_rollups,
        "compiled_report": compiled_report,
    }

    loan_result_dir = S3_RESULT_DIR / loan_id
    write_json(loan_result_dir / "scorecard.json", scorecard)

    update_status(loan_id, current_node="scorecard", errors=errors, node_history=history)

    return {
        **state,
        "scorecard": scorecard,
        "errors": errors,
        "node_history": history,
    }

