import logging
import shutil

from config import LOS_RECEIVED_DIR, S3_RESULT_DIR

from pipeline.state import PipelineState
from pipeline.storage import copy_file, update_status, write_json

logger = logging.getLogger("disbursement_pipeline.node6_push")


def node6_push(state: PipelineState) -> PipelineState:
    """Node 6 (Push) — Write scorecard to S3 result + copy into mock LOS received folder."""
    loan_id = state["loan_id"]
    errors = list(state.get("errors", []))
    history = list(state.get("node_history", []))
    history.append("push")

    logger.info("Executing Node 6 (Push) for loan %s", loan_id)

    scorecard = state.get("scorecard", {})
    loan_result_dir = S3_RESULT_DIR / loan_id
    scorecard_path = loan_result_dir / "scorecard.json"

    # Persist in S3 result
    write_json(scorecard_path, scorecard)

    # Copy to mock LOS scorecards_received
    LOS_RECEIVED_DIR.mkdir(parents=True, exist_ok=True)
    los_scorecard_path = LOS_RECEIVED_DIR / f"{loan_id}_scorecard.json"
    try:
        copy_file(scorecard_path, los_scorecard_path)
    except (OSError, shutil.Error) as e:
        msg = f"Failed to push scorecard to LOS received folder: {e}"
        logger.error(msg)
        errors.append(msg)

    # Final status update
    history.append("done")
    update_status(loan_id, current_node="done", errors=errors, node_history=history)

    return {
        **state,
        "errors": errors,
        "node_history": history,
    }

