"""Node: Push Results — Writes final decision artifacts and notifies external LOS / sinks."""
import logging
import shutil
from pathlib import Path

from config import LOS_RECEIVED_DIR, S3_RESULT_DIR
from pipeline.state import PipelineState
from pipeline.storage import copy_file, save_s3_result, update_status

logger = logging.getLogger("disbursement_pipeline.push_results")


def push_results(state: PipelineState) -> PipelineState:
    """Pushes final scorecard to S3 Result tier and copies to mock LOS received directory."""
    loan_id = state["loan_id"]
    errors = list(state.get("errors", []))
    history = list(state.get("node_history", []))
    history.append("push_results")

    logger.info("Executing push_results for loan: %s", loan_id)

    scorecard = state.get("scorecard", {})
    scorecard_path = save_s3_result(loan_id, "scorecard.json", scorecard)

    # Copy to mock LOS scorecards_received
    LOS_RECEIVED_DIR.mkdir(parents=True, exist_ok=True)
    los_target = LOS_RECEIVED_DIR / f"{loan_id}_scorecard.json"
    try:
        copy_file(scorecard_path, los_target)
        logger.info("Successfully pushed scorecard to LOS received folder for %s", loan_id)
    except (OSError, shutil.Error) as e:
        msg = f"Failed to push scorecard to LOS received folder: {e}"
        logger.error(msg)
        errors.append(msg)

    history.append("done")
    update_status(loan_id, current_node="done", errors=errors, node_history=history)

    return {
        **state,
        "errors": errors,
        "node_history": history,
    }
